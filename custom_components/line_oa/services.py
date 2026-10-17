"""Service actions for the LINE OA integration.

Exactly three actions are registered: ``send_text``, ``send_image`` and
``send_screen_shot``. Each sends to a single Gateway alias (``who``) exactly
once; nothing here retries a send.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

import probatio as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .api import (
    AliasNotFoundError,
    EndpointNotFoundError,
    GatewayApiError,
    GatewayAuthError,
    GatewayConnectionError,
    GatewayUnavailableError,
    InvalidRequestError,
    LineOAError,
    LineRejectedError,
    LineSessionError,
    PayloadTooLargeError,
    RateLimitedError,
    SendResult,
    SendUnknownError,
)
from .const import (
    ATTR_CAMERA_ENTITY,
    ATTR_IMAGE_PATH,
    ATTR_MESSAGE,
    ATTR_WHO,
    DOMAIN,
    LOGGER,
    MAX_MESSAGE_LENGTH,
    MAX_WHO_LENGTH,
    SERVICE_SEND_IMAGE,
    SERVICE_SEND_SCREEN_SHOT,
    SERVICE_SEND_TEXT,
)
from .coordinator import LineOAConfigEntry
from .images import LoadedImage, async_capture_camera_image, async_load_local_image


def single_who(value: Any) -> str:
    """Validate ``who``: exactly one non-empty alias string, never a list."""
    if isinstance(value, (list, tuple, set, dict)):
        raise vol.Invalid(
            "who must be a single alias string; sending to multiple aliases is not "
            "supported (create a LINE group and give it one alias instead)"
        )
    text = cv.string(value).strip()
    if not text:
        raise vol.Invalid("who must not be empty")
    if len(text) > MAX_WHO_LENGTH:
        raise vol.Invalid(f"who must be at most {MAX_WHO_LENGTH} characters")
    return text


def _message(value: Any) -> str:
    text = cv.string(value)
    if not text.strip():
        raise vol.Invalid("message must not be empty")
    if len(text) > MAX_MESSAGE_LENGTH:
        raise vol.Invalid(f"message must be at most {MAX_MESSAGE_LENGTH} characters")
    return text


def _no_message(value: Any) -> Any:
    """Images are sent without text; reject ``message`` with a clear hint."""
    if isinstance(value, dict) and ATTR_MESSAGE in value:
        raise vol.Invalid(
            "message is not supported for images; send the text separately with line_oa.send_text"
        )
    return value


SEND_TEXT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_WHO): single_who,
        vol.Required(ATTR_MESSAGE): _message,
    }
)
SEND_IMAGE_SCHEMA = vol.All(
    _no_message,
    vol.Schema(
        {
            vol.Required(ATTR_WHO): single_who,
            vol.Required(ATTR_IMAGE_PATH): vol.All(cv.string, str.strip, vol.Length(min=1)),
        }
    ),
)
SEND_SCREEN_SHOT_SCHEMA = vol.All(
    _no_message,
    vol.Schema(
        {
            vol.Required(ATTR_WHO): single_who,
            # cv.string rejects lists, so exactly one camera entity is accepted.
            vol.Required(ATTR_CAMERA_ENTITY): vol.All(cv.string, cv.entity_domain("camera")),
        }
    ),
)


def _get_entry(hass: HomeAssistant) -> LineOAConfigEntry:
    entries: list[LineOAConfigEntry] = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    return entries[0]


def _error(key: str, **placeholders: str) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


def _reason(err: LineOAError) -> str:
    return err.detail or err.code or "-"


def _translate_error(
    hass: HomeAssistant, entry: LineOAConfigEntry, err: LineOAError, who: str
) -> HomeAssistantError:
    """Map a Gateway client error to a meaningful Home Assistant error.

    Only errors that say something about Gateway or session state trigger a
    status refresh; the Gateway stays the source of truth for login state.
    """
    coordinator = entry.runtime_data
    if isinstance(err, SendUnknownError):
        LOGGER.warning(
            "LINE OA send result is unknown (request_id=%s, reason=%s). "
            "The message may have been delivered; it will not be resent",
            err.request_id,
            _reason(err),
        )
        return _error("send_unknown", request_id=err.request_id)
    if isinstance(err, GatewayAuthError):
        entry.async_start_reauth(hass)
        return _error("invalid_api_key")
    if isinstance(err, GatewayConnectionError):
        entry.async_create_task(hass, coordinator.async_request_refresh())
        return _error("cannot_connect", error=_reason(err))
    if isinstance(err, AliasNotFoundError):
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="alias_not_found",
            translation_placeholders={"who": who},
        )
    if isinstance(err, LineSessionError):
        entry.async_create_task(hass, coordinator.async_request_refresh())
        if err.code in ("AUTH_REQUIRED", "NOT_LOGGED_IN"):
            return _error("line_auth_required")
        return _error("line_session_expired")
    if isinstance(err, LineRejectedError):
        return _error("line_rejected", reason=_reason(err))
    if isinstance(err, RateLimitedError):
        return _error("rate_limited", retry_after=err.retry_after or "-")
    if isinstance(err, PayloadTooLargeError):
        return _error("gateway_payload_too_large")
    if isinstance(err, GatewayUnavailableError):
        entry.async_create_task(hass, coordinator.async_request_refresh())
        return _error("gateway_unavailable")
    if isinstance(err, EndpointNotFoundError):
        return _error("endpoint_not_found")
    if isinstance(err, (InvalidRequestError, GatewayApiError)):
        return _error("gateway_rejected_request", reason=_reason(err))
    return _error("gateway_error", error=_reason(err))


async def _async_check_alias(hass: HomeAssistant, entry: LineOAConfigEntry, who: str) -> None:
    """Fail early (before reading files or cameras) if ``who`` is unknown.

    The Gateway validates the alias again when sending.
    """
    try:
        aliases = await entry.runtime_data.client.async_get_aliases()
    except EndpointNotFoundError:
        LOGGER.debug("Gateway has no aliases endpoint; relying on send-time validation")
        return
    except LineOAError as err:
        raise _translate_error(hass, entry, err, who) from err
    if who not in aliases:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="alias_not_found",
            translation_placeholders={"who": who},
        )


async def _async_send(
    hass: HomeAssistant,
    entry: LineOAConfigEntry,
    who: str,
    kind: str,
    send: Awaitable[SendResult],
) -> ServiceResponse:
    try:
        result = await send
    except LineOAError as err:
        raise _translate_error(hass, entry, err, who) from err
    LOGGER.debug(
        "LINE OA %s to %s: %s (request_id=%s)", kind, who, result.status, result.request_id
    )
    return {
        "status": str(result.status),
        "request_id": result.request_id,
        "message_id": result.message_id,
        "who": who,
    }


async def _async_send_image(
    hass: HomeAssistant,
    entry: LineOAConfigEntry,
    who: str,
    image: LoadedImage,
    *,
    kind: str,
) -> ServiceResponse:
    client = entry.runtime_data.client
    return await _async_send(
        hass,
        entry,
        who,
        kind,
        client.async_send_image(who, image.content, image.content_type, image.filename),
    )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the LINE OA actions."""

    async def send_text(call: ServiceCall) -> ServiceResponse:
        entry = _get_entry(hass)
        who: str = call.data[ATTR_WHO]
        await _async_check_alias(hass, entry, who)
        return await _async_send(
            hass,
            entry,
            who,
            "text",
            entry.runtime_data.client.async_send_text(who, call.data[ATTR_MESSAGE]),
        )

    async def send_image(call: ServiceCall) -> ServiceResponse:
        entry = _get_entry(hass)
        who: str = call.data[ATTR_WHO]
        await _async_check_alias(hass, entry, who)
        image = await async_load_local_image(hass, call.data[ATTR_IMAGE_PATH])
        return await _async_send_image(hass, entry, who, image, kind="image")

    async def send_screen_shot(call: ServiceCall) -> ServiceResponse:
        entry = _get_entry(hass)
        who: str = call.data[ATTR_WHO]
        await _async_check_alias(hass, entry, who)
        image = await async_capture_camera_image(hass, call.data[ATTR_CAMERA_ENTITY])
        return await _async_send_image(hass, entry, who, image, kind="screen shot")

    for name, handler, schema in (
        (SERVICE_SEND_TEXT, send_text, SEND_TEXT_SCHEMA),
        (SERVICE_SEND_IMAGE, send_image, SEND_IMAGE_SCHEMA),
        (SERVICE_SEND_SCREEN_SHOT, send_screen_shot, SEND_SCREEN_SHOT_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN, name, handler, schema=schema, supports_response=SupportsResponse.OPTIONAL
        )
