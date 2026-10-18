"""Bounded, credential-free downloads of public JPEG/PNG images."""

from __future__ import annotations

import asyncio
from ipaddress import ip_address
import socket

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.resolver import ThreadedResolver
from yarl import URL

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import DOMAIN, MAX_IMAGE_BYTES
from .images import LoadedImage, detect_image_type

IMAGE_DOWNLOAD_TIMEOUT = 20
MAX_IMAGE_REDIRECTS = 3
_REDIRECTS = {301, 302, 303, 307, 308}


def _is_public(address: str) -> bool:
    """Require a globally routable unicast address, including mapped IPv4."""
    ip = ip_address(address)
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def _invalid(key: str) -> ServiceValidationError:
    return ServiceValidationError(translation_domain=DOMAIN, translation_key=key)


def _error(key: str, **placeholders: str) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


def _validate_url(value: str | URL) -> URL:
    """Validate each hop before connecting; never include signed URLs in errors."""
    text = str(value)
    if len(text) > 8192 or any(char.isspace() or ord(char) < 32 for char in text):
        raise _invalid("image_url_invalid")
    try:
        # Preserve signed query strings and already encoded path bytes.
        url = URL(text, encoded=True)
        host = url.host
        port = url.port
        if (
            url.scheme not in {"http", "https"}
            or not host
            or port is None
            or not 1 <= port <= 65535
            or url.user is not None
            or url.password is not None
            or url.fragment
        ):
            raise ValueError
    except ValueError, UnicodeError:
        raise _invalid("image_url_invalid") from None
    try:
        ip_address(host)
    except ValueError:
        return url
    if not _is_public(host):
        raise _invalid("image_url_not_allowed")
    return url


class PublicImageResolver(AbstractResolver):
    """Filter DNS at the connector, so the socket uses the validated address."""

    def __init__(self) -> None:
        """Use event-loop DNS, without adding an optional resolver dependency."""
        self._resolver = ThreadedResolver()

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        """Reject private or mixed public/private answers before opening a socket."""
        records = await self._resolver.resolve(host, port, family)
        if not records or any(not _is_public(record["host"]) for record in records):
            raise _invalid("image_url_not_allowed")
        return records

    async def close(self) -> None:
        """Release the resolver; the connector does not own injected resolvers."""
        await self._resolver.close()


async def _download(session: aiohttp.ClientSession, source: str) -> LoadedImage:
    url = _validate_url(source)
    for hop in range(MAX_IMAGE_REDIRECTS + 1):
        async with session.get(url, allow_redirects=False) as response:
            if response.status in _REDIRECTS:
                if hop == MAX_IMAGE_REDIRECTS:
                    raise _error("image_url_redirect_limit")
                location = response.headers.get("Location")
                if not location:
                    raise _error("image_url_download_failed")
                try:
                    next_url = _validate_url(url.join(URL(location, encoded=True)))
                except ValueError, UnicodeError:
                    raise _invalid("image_url_invalid") from None
                if url.scheme == "https" and next_url.scheme != "https":
                    raise _invalid("image_url_not_allowed")
                url = next_url
                continue
            if response.status != 200:
                raise _error("image_url_http_error", status=str(response.status))
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise _error("image_url_download_failed")
            if response.content_length is not None and response.content_length > MAX_IMAGE_BYTES:
                raise _error("image_url_too_large")
            content = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                if len(content) + len(chunk) > MAX_IMAGE_BYTES:
                    raise _error("image_url_too_large")
                content.extend(chunk)
            image_content = bytes(content)
            content_type = detect_image_type(image_content)
            if content_type is None:
                raise _error("image_url_invalid_format")
            extension = "png" if content_type == "image/png" else "jpg"
            return LoadedImage(image_content, content_type, f"remote.{extension}")
    raise _error("image_url_redirect_limit")


async def async_download_image(source: str) -> LoadedImage:
    """Download into bounded RAM; no disk cache, cookies, auth or proxy forwarding."""
    _validate_url(source)
    resolver = PublicImageResolver()
    try:
        async with asyncio.timeout(IMAGE_DOWNLOAD_TIMEOUT):
            # A dedicated connector pins validated DNS answers, and a fresh dummy
            # cookie jar prevents any HA/Gateway credentials reaching the image host.
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False),
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
                auto_decompress=False,
                timeout=aiohttp.ClientTimeout(total=IMAGE_DOWNLOAD_TIMEOUT, sock_connect=10),
                headers={"Accept": "image/jpeg, image/png", "Accept-Encoding": "identity"},
            ) as session:
                return await _download(session, source)
    except TimeoutError:
        raise _error("image_url_timeout") from None
    except aiohttp.ClientError, OSError:
        # aiohttp exception text can contain query-string credentials. Do not chain it.
        raise _error("image_url_download_failed") from None
    finally:
        await resolver.close()
