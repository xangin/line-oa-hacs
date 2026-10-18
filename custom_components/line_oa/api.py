"""Async HTTP client for the LINE OA Gateway.

This module has no Home Assistant dependencies so it can be tested in
isolation. All errors are raised as subclasses of ``LineOAError``.

Safety rules implemented here:

* The API key is only ever placed in the ``Authorization`` header and is never
  logged or included in exception text.
* POST requests are never retried, and redirects are never followed (a
  redirect would make aiohttp re-submit the request body).
* If a POST may have reached the Gateway but the outcome is not known
  (timeout while waiting for the response, connection dropped, 5xx from a
  proxy, unparsable 2xx body), ``SendUnknownError`` is raised so the caller can
  tell the user not to retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import logging
import re
from typing import Any
import uuid

import aiohttp
from yarl import URL

_LOGGER = logging.getLogger(__name__)

API_HEALTH = "/api/v1/health"
API_STATUS = "/api/v1/status"
API_ALIASES = "/api/v1/aliases"
API_MESSAGES_TEXT = "/api/v1/messages/text"
API_MESSAGES_IMAGE = "/api/v1/messages/image"
API_PAIRING_EXCHANGE = "/api/v1/pairing/exchange"

# Gateway API major version this integration speaks. Older LXC Gateways do not
# report ``api_version`` at all; they implement version 1 and stay supported.
SUPPORTED_API_VERSION = 1
PAIRING_CLIENT = "homeassistant-line_oa"
PAIRING_TIMEOUT = 15  # seconds

GET_TIMEOUT = 10  # seconds
TEXT_POST_TIMEOUT = 30  # seconds
IMAGE_POST_TIMEOUT = 60  # seconds
CONNECT_TIMEOUT = 10  # seconds
MAX_RESPONSE_BYTES = 1024 * 1024

MAX_ERROR_TEXT_LENGTH = 255

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\bbearer\s+\S+"), "Bearer [REDACTED]"),
    (
        re.compile(
            r"(?i)\b(set-cookie|cookie|authorization|x-api-key|api[_-]?key|"
            r"access[_-]?token|refresh[_-]?token|token|password|passwd|secret|"
            r"session[_-]?id|sid)\b(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
        ),
        r"\1\2[REDACTED]",
    ),
    # Long opaque strings (JWTs, cookies, hex/base64 secrets).
    (re.compile(r"[A-Za-z0-9+/_\-.=]{32,}"), "[REDACTED]"),
)
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")


def sanitize_error_text(value: Any, max_length: int = MAX_ERROR_TEXT_LENGTH) -> str:
    """Return a short, single-line error summary with secrets redacted."""
    text = str(value)
    text = _CONTROL_CHARS.sub(" ", text)
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    text = " ".join(text.split())
    if len(text) > max_length:
        text = text[: max_length - 1].rstrip() + "…"
    return text


class SendStatus(StrEnum):
    """Send outcome reported by the Gateway."""

    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------


class LineOAError(Exception):
    """Base error for the LINE OA Gateway client."""

    def __init__(
        self,
        detail: str | None = None,
        *,
        code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        """Initialize the error. ``detail`` must already be safe to display."""
        self.detail = detail
        self.code = code
        self.http_status = http_status
        super().__init__(detail or code or self.__class__.__name__)


class GatewayConnectionError(LineOAError):
    """The Gateway could not be reached; the request was not delivered."""


class GatewayAuthError(LineOAError):
    """The Gateway rejected the API key (HTTP 401/403)."""


class GatewayResponseError(LineOAError):
    """The Gateway returned an unexpected or invalid response."""


class EndpointNotFoundError(GatewayResponseError):
    """The Gateway does not implement the requested endpoint."""


class GatewayApiError(LineOAError):
    """The Gateway returned an explicit error; the message was not sent."""


class AliasNotFoundError(GatewayApiError):
    """The recipient alias is not configured on the Gateway."""


class LineSessionError(GatewayApiError):
    """The Gateway's LINE session is expired or requires login."""


class LineRejectedError(GatewayApiError):
    """LINE explicitly rejected the message (send status ``failed``)."""


class RateLimitedError(GatewayApiError):
    """The Gateway or LINE rate limited the request."""

    def __init__(
        self,
        detail: str | None = None,
        *,
        code: str | None = None,
        http_status: int | None = None,
        retry_after: str | None = None,
    ) -> None:
        """Initialize the error."""
        super().__init__(detail, code=code, http_status=http_status)
        self.retry_after = retry_after


class PayloadTooLargeError(GatewayApiError):
    """The uploaded image was rejected as too large."""


class UnsupportedFieldError(GatewayApiError):
    """The Gateway does not support a submitted field."""


class InvalidRequestError(GatewayApiError):
    """The Gateway rejected the request as invalid."""


class GatewayUnavailableError(GatewayApiError):
    """The Gateway is temporarily unable to process requests (HTTP 503)."""


class PairingError(LineOAError):
    """Pairing with a Gateway app failed; no credential was obtained."""


class PairingRejectedError(PairingError):
    """The one-time pairing token was invalid, expired or already used."""


class PairingRateLimitedError(PairingError):
    """Too many pairing attempts."""


class PairingUnavailableError(PairingError):
    """The Gateway does not offer pairing (endpoint missing or unreachable)."""


class SendUnknownError(LineOAError):
    """The send outcome is unknown. The message may have been delivered."""

    def __init__(
        self,
        detail: str | None = None,
        *,
        request_id: str,
        code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        """Initialize the error."""
        super().__init__(detail, code=code, http_status=http_status)
        self.request_id = request_id


# Standard Gateway error codes -> exception class.
# Codes from the contract: ALIAS_NOT_FOUND, SESSION_EXPIRED, SEND_UNKNOWN.
# The remaining codes are documented assumptions, see docs/api-contract-issues.md.
SESSION_ERROR_CODES = frozenset(
    {"SESSION_EXPIRED", "AUTH_REQUIRED", "NOT_LOGGED_IN", "SESSION_DISCONNECTED"}
)
_ERROR_CODE_MAP: dict[str, type[LineOAError]] = {
    "UNAUTHORIZED": GatewayAuthError,
    "INVALID_API_KEY": GatewayAuthError,
    "FORBIDDEN": GatewayAuthError,
    "ALIAS_NOT_FOUND": AliasNotFoundError,
    **dict.fromkeys(SESSION_ERROR_CODES, LineSessionError),
    "LINE_REJECTED": LineRejectedError,
    "SEND_FAILED": LineRejectedError,
    "RATE_LIMITED": RateLimitedError,
    "PAYLOAD_TOO_LARGE": PayloadTooLargeError,
    "IMAGE_TOO_LARGE": PayloadTooLargeError,
    "MESSAGE_NOT_SUPPORTED": UnsupportedFieldError,
    "UNSUPPORTED_FIELD": UnsupportedFieldError,
    "INVALID_REQUEST": InvalidRequestError,
    "VALIDATION_ERROR": InvalidRequestError,
    "INVALID_IMAGE": InvalidRequestError,
    "GATEWAY_UNAVAILABLE": GatewayUnavailableError,
    "SERVICE_UNAVAILABLE": GatewayUnavailableError,
}
CODE_SEND_UNKNOWN = "SEND_UNKNOWN"


# --------------------------------------------------------------------------
# Data models
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GatewayStatus:
    """Parsed ``GET /api/v1/status`` response."""

    instance_id: str
    version: str | None
    gateway_status: str | None
    line_logged_in: bool | None
    session_status: str | None
    account_name: str | None
    last_error: str | None
    updated_at: str | None
    api_version: int | None = None
    login_required_reason: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> GatewayStatus:
        """Create from the Gateway JSON payload."""
        if not isinstance(data, dict):
            raise GatewayResponseError("Status response is not a JSON object")
        instance_id = data.get("instance_id")
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise GatewayResponseError("Status response has no instance_id")
        logged_in = data.get("line_logged_in")
        return cls(
            instance_id=instance_id.strip(),
            api_version=parse_api_version(data.get("api_version")),
            version=_opt_str(data.get("version")),
            gateway_status=_opt_str(data.get("gateway_status")),
            line_logged_in=logged_in if isinstance(logged_in, bool) else None,
            session_status=_opt_str(data.get("session_status")),
            account_name=_opt_str(data.get("account_name")),
            last_error=_error_summary(data.get("last_error")),
            updated_at=_opt_str(data.get("updated_at")),
            login_required_reason=_opt_str(data.get("login_required_reason")),
        )


def parse_api_version(value: Any) -> int | None:
    """Return the reported API version; ``None`` when not reported (legacy v1).

    Malformed values are returned as ``0`` so they are treated as unsupported.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def api_version_supported(version: int | None) -> bool:
    """Return True for API version 1 or a Gateway that does not report one."""
    return version is None or version == SUPPORTED_API_VERSION


@dataclass(frozen=True, slots=True)
class PairingResult:
    """Credential issued by a Gateway app during pairing."""

    instance_id: str
    api_key: str
    api_version: int

    def __repr__(self) -> str:
        """Never include the credential in a repr."""
        return f"PairingResult(instance_id={self.instance_id!r}, api_version={self.api_version})"


_API_KEY_FORMAT = re.compile(r"[A-Za-z0-9_\-]{32,256}")


@dataclass(frozen=True, slots=True)
class SendResult:
    """Successful send result (``submitted`` or ``confirmed``)."""

    status: SendStatus
    request_id: str
    message_id: str | None = None


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _error_summary(value: Any) -> str | None:
    """Turn a ``last_error`` value (null, string or object) into a summary."""
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("message") or value.get("code") or value.get("error")
        if value is None:
            return None
    text = str(value).strip()
    return sanitize_error_text(text) if text else None


def _extract_error(body: Any) -> tuple[str | None, str | None]:
    """Extract ``(code, message)`` from a Gateway error body.

    Accepts ``{"error": {"code": ..., "message": ...}}``,
    ``{"error": "CODE"}`` and ``{"code": ..., "message": ...}``.
    """
    if not isinstance(body, dict):
        return None, None
    code: Any = None
    message: Any = None
    error = body.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
    elif isinstance(error, str):
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", error):
            code = error
        else:
            message = error
    code = code or body.get("code") or body.get("error_code")
    message = message or body.get("message") or body.get("detail")
    code_text = str(code).strip().upper() if code else None
    message_text = sanitize_error_text(message) if message else None
    return code_text or None, message_text or None


@dataclass(frozen=True, slots=True)
class _RawResponse:
    status: int
    body: Any
    is_json: bool
    retry_after: str | None


def _parse_body(raw: bytes) -> tuple[Any, bool]:
    if not raw:
        return None, False
    try:
        return json.loads(raw), True
    except ValueError:
        return None, False


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


def normalize_base_url(url: str) -> str:
    """Validate and normalize a Gateway base URL.

    Raises ``ValueError`` if the URL is not an absolute http(s) URL.
    """
    parsed = URL(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.host:
        raise ValueError("Gateway URL must be an absolute http:// or https:// URL")
    if parsed.query_string or parsed.fragment or parsed.user:
        raise ValueError("Gateway URL must not contain credentials, query or fragment")
    return str(parsed).rstrip("/")


class LineOAGatewayClient:
    """Client for the LINE OA Gateway HTTP API."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_key: str) -> None:
        """Initialize the client."""
        self._session = session
        self._base_url = normalize_base_url(base_url)
        self._api_key = api_key

    @property
    def base_url(self) -> str:
        """Return the normalized Gateway base URL."""
        return self._base_url

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def _raw_request(
        self,
        method: str,
        path: str,
        *,
        request_timeout: float,
        json_body: dict[str, Any] | None = None,
        form: aiohttp.FormData | None = None,
    ) -> _RawResponse:
        """Perform one HTTP request. Raises aiohttp/asyncio errors unchanged."""
        async with self._session.request(
            method,
            f"{self._base_url}{path}",
            headers=self._headers(),
            json=json_body,
            data=form,
            timeout=aiohttp.ClientTimeout(total=request_timeout, sock_connect=CONNECT_TIMEOUT),
            allow_redirects=False,
        ) as resp:
            raw = await resp.content.read(MAX_RESPONSE_BYTES)
            body, is_json = _parse_body(raw)
            return _RawResponse(resp.status, body, is_json, resp.headers.get("Retry-After"))

    # ---------------------------------------------------------------- GET

    async def _get(self, path: str) -> _RawResponse:
        try:
            resp = await self._raw_request("GET", path, request_timeout=GET_TIMEOUT)
        except TimeoutError as err:
            raise GatewayConnectionError("Timeout while contacting the Gateway") from err
        except aiohttp.ClientConnectorDNSError as err:
            raise GatewayConnectionError("Gateway host name could not be resolved") from err
        except aiohttp.ClientConnectorError as err:
            raise GatewayConnectionError("Could not connect to the Gateway") from err
        except aiohttp.ClientError as err:
            raise GatewayConnectionError(f"Gateway connection error: {type(err).__name__}") from err
        if resp.status in (401, 403):
            code, _ = _extract_error(resp.body)
            raise GatewayAuthError(
                "Gateway rejected the API key", code=code, http_status=resp.status
            )
        if not 200 <= resp.status < 300:
            code, message = _extract_error(resp.body)
            error_cls = (
                EndpointNotFoundError
                if resp.status == 404 and code is None
                else (GatewayResponseError)
            )
            raise error_cls(
                message or f"Gateway returned HTTP {resp.status}",
                code=code,
                http_status=resp.status,
            )
        return resp

    async def async_health(self) -> dict[str, Any]:
        """Call ``GET /api/v1/health``. Any 2xx response means healthy."""
        resp = await self._get(API_HEALTH)
        return resp.body if isinstance(resp.body, dict) else {}

    async def async_pairing_exchange(self, instance_id: str, bootstrap_token: str) -> PairingResult:
        """Exchange a one-time bootstrap token for an API key (Gateway app only).

        Sent exactly once: an ambiguous outcome is reported as a failure and the
        user re-authorizes pairing in the app's Web UI. The token and the issued
        key are never logged or included in exceptions.
        """
        payload = {
            "instance_id": instance_id,
            "bootstrap_token": bootstrap_token,
            "client": PAIRING_CLIENT,
            "request_id": str(uuid.uuid4()),
        }
        try:
            resp = await self._raw_request(
                "POST", API_PAIRING_EXCHANGE, request_timeout=PAIRING_TIMEOUT, json_body=payload
            )
        except aiohttp.ClientConnectorError as err:
            raise PairingUnavailableError("Could not connect to the Gateway app") from err
        except (TimeoutError, aiohttp.ClientError) as err:
            raise PairingError(f"Pairing request failed: {type(err).__name__}") from err
        code, _ = _extract_error(resp.body)
        if resp.status in (401, 403):
            raise PairingRejectedError("Pairing token rejected", code=code, http_status=resp.status)
        if resp.status == 429:
            raise PairingRateLimitedError("Too many pairing attempts", code=code, http_status=429)
        if resp.status == 404:
            raise PairingUnavailableError("Pairing is not supported", code=code, http_status=404)
        if resp.status != 201 or not isinstance(resp.body, dict):
            raise PairingError(
                f"Unexpected pairing response (HTTP {resp.status})",
                code=code,
                http_status=resp.status,
            )
        body = resp.body
        api_key = body.get("api_key")
        version = parse_api_version(body.get("api_version"))
        if (
            body.get("instance_id") != instance_id
            or not isinstance(api_key, str)
            or not _API_KEY_FORMAT.fullmatch(api_key)
            or version != SUPPORTED_API_VERSION
        ):
            raise PairingError("Pairing response failed validation", http_status=resp.status)
        return PairingResult(instance_id=instance_id, api_key=api_key, api_version=version)

    async def async_get_status(self) -> GatewayStatus:
        """Call ``GET /api/v1/status``."""
        resp = await self._get(API_STATUS)
        if not resp.is_json:
            raise GatewayResponseError("Status response is not JSON")
        return GatewayStatus.from_dict(resp.body)

    async def async_get_aliases(self) -> list[str]:
        """Call ``GET /api/v1/aliases`` and return the configured alias names."""
        resp = await self._get(API_ALIASES)
        body = resp.body
        if isinstance(body, dict):
            body = body.get("aliases")
        if not isinstance(body, list):
            raise GatewayResponseError("Aliases response has an unexpected format")
        aliases: list[str] = []
        for item in body:
            name = item.get("alias") if isinstance(item, dict) else item
            if isinstance(name, str) and name.strip():
                aliases.append(name.strip())
        return aliases

    # ---------------------------------------------------------------- POST

    async def async_send_text(self, who: str, message: str) -> SendResult:
        """Send a text message via ``POST /api/v1/messages/text``."""
        request_id = str(uuid.uuid4())
        payload = {"who": who, "message": message, "request_id": request_id}
        return await self._post_message(
            API_MESSAGES_TEXT, request_id, TEXT_POST_TIMEOUT, json_body=payload
        )

    async def async_send_image(
        self,
        who: str,
        image: bytes,
        content_type: str,
        filename: str,
    ) -> SendResult:
        """Upload an image via ``POST /api/v1/messages/image`` (multipart).

        Images are sent without accompanying text: the form contains only
        ``who``, ``image`` and ``request_id``.
        """
        request_id = str(uuid.uuid4())
        form = aiohttp.FormData()
        form.add_field("who", who)
        form.add_field("image", image, filename=filename, content_type=content_type)
        form.add_field("request_id", request_id)
        return await self._post_message(
            API_MESSAGES_IMAGE, request_id, IMAGE_POST_TIMEOUT, form=form
        )

    async def _post_message(
        self,
        path: str,
        request_id: str,
        request_timeout: float,
        *,
        json_body: dict[str, Any] | None = None,
        form: aiohttp.FormData | None = None,
    ) -> SendResult:
        """Send a message request exactly once and classify the outcome."""
        try:
            resp = await self._raw_request(
                "POST", path, request_timeout=request_timeout, json_body=json_body, form=form
            )
        except aiohttp.ConnectionTimeoutError as err:
            raise GatewayConnectionError("Timeout while connecting to the Gateway") from err
        except aiohttp.ClientConnectorDNSError as err:
            raise GatewayConnectionError("Gateway host name could not be resolved") from err
        except aiohttp.ClientConnectorError as err:
            # Includes TLS failures: the request body was never sent.
            raise GatewayConnectionError("Could not connect to the Gateway") from err
        except TimeoutError as err:
            raise SendUnknownError(
                "Timed out waiting for the Gateway response", request_id=request_id
            ) from err
        except aiohttp.ClientError as err:
            raise SendUnknownError(
                f"Connection lost during send: {type(err).__name__}",
                request_id=request_id,
            ) from err

        _LOGGER.debug("Gateway %s request %s -> HTTP %s", path, request_id, resp.status)
        if 200 <= resp.status < 300:
            return self._parse_send_success(resp, request_id)
        raise self._send_error(resp, request_id)

    @staticmethod
    def _parse_send_success(resp: _RawResponse, request_id: str) -> SendResult:
        body = resp.body
        if not resp.is_json or not isinstance(body, dict):
            raise SendUnknownError(
                "Gateway accepted the request but the response was not valid JSON",
                request_id=request_id,
                http_status=resp.status,
            )
        status_value = str(body.get("status", "")).strip().lower()
        try:
            status = SendStatus(status_value)
        except ValueError:
            raise SendUnknownError(
                "Gateway response did not contain a known send status",
                request_id=request_id,
                http_status=resp.status,
            ) from None
        if status in (SendStatus.SUBMITTED, SendStatus.CONFIRMED):
            return SendResult(
                status=status,
                request_id=_opt_str(body.get("request_id")) or request_id,
                message_id=_opt_str(body.get("message_id")),
            )
        code, message = _extract_error(body)
        if status is SendStatus.UNKNOWN or code == CODE_SEND_UNKNOWN:
            raise SendUnknownError(
                message, request_id=request_id, code=code, http_status=resp.status
            )
        # status == failed: LINE (or the Gateway) explicitly did not send it.
        error_cls = _ERROR_CODE_MAP.get(code or "", LineRejectedError)
        if not issubclass(error_cls, GatewayApiError):
            error_cls = LineRejectedError
        raise error_cls(message, code=code, http_status=resp.status)

    @staticmethod
    def _send_error(resp: _RawResponse, request_id: str) -> LineOAError:
        code, message = _extract_error(resp.body)
        status_value = (
            str(resp.body.get("status", "")).lower() if isinstance(resp.body, dict) else ""
        )
        if code == CODE_SEND_UNKNOWN or status_value == SendStatus.UNKNOWN:
            return SendUnknownError(
                message, request_id=request_id, code=code, http_status=resp.status
            )
        if code and code in _ERROR_CODE_MAP:
            error_cls = _ERROR_CODE_MAP[code]
            if error_cls is RateLimitedError:
                return RateLimitedError(
                    message, code=code, http_status=resp.status, retry_after=resp.retry_after
                )
            return error_cls(message, code=code, http_status=resp.status)
        status = resp.status
        if status in (401, 403):
            return GatewayAuthError("Gateway rejected the API key", code=code, http_status=status)
        if status == 404 and code is None:
            return EndpointNotFoundError(
                "Gateway endpoint not found", code=code, http_status=status
            )
        if status == 413:
            return PayloadTooLargeError(message, code=code, http_status=status)
        if status == 429:
            return RateLimitedError(
                message, code=code, http_status=status, retry_after=resp.retry_after
            )
        if status == 503:
            return GatewayUnavailableError(message, code=code, http_status=status)
        if status >= 500:
            # 500/502/504 (or unknown 5xx): the Gateway or a proxy failed while
            # handling the request. It may already have been sent to LINE.
            return SendUnknownError(
                message or f"Gateway returned HTTP {status}",
                request_id=request_id,
                code=code,
                http_status=status,
            )
        # Other 4xx (400, 404 with unknown code, 409, 422, ...): the Gateway
        # refused the request explicitly.
        return InvalidRequestError(
            message or f"Gateway rejected the request (HTTP {status})",
            code=code,
            http_status=status,
        )
