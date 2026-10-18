"""Public image downloads and multipart upload, using local mock HTTP servers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
import socket
import ssl
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.line_oa.const import DOMAIN, MAX_IMAGE_BYTES
from custom_components.line_oa.remote_images import (
    PublicImageResolver,
    _validate_url,
    async_download_image,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .mock_gateway import MockGatewayState

JPEG = b"\xff\xd8\xff" + b"synthetic jpeg"
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic png"


@dataclass
class ImageSource:
    """A controllable HTTP image source, separate from the Gateway."""

    url: str = ""
    body: bytes = JPEG
    requests: list[web.Request] = field(default_factory=list)
    release: asyncio.Event = field(default_factory=asyncio.Event)


@pytest.fixture
async def source(
    socket_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[ImageSource]:
    state = ImageSource()

    async def handler(request: web.Request) -> web.StreamResponse:
        state.requests.append(request)
        if request.path == "/redirect":
            return web.Response(
                status=302, headers={"Location": "/image", "Set-Cookie": "no_leak=1"}
            )
        if request.path == "/loop":
            return web.Response(status=302, headers={"Location": "/loop"})
        if request.path == "/private":
            return web.Response(status=302, headers={"Location": "http://127.0.0.1/private"})
        if request.path == "/missing":
            return web.Response(status=404)
        if request.path == "/slow":
            await state.release.wait()
        if request.path == "/large-header":
            response = web.StreamResponse(headers={"Content-Length": str(MAX_IMAGE_BYTES + 1)})
            await response.prepare(request)
            return response
        if request.path == "/chunked":
            response = web.StreamResponse()
            await response.prepare(request)
            await response.write(state.body)
            await response.write_eof()
            return response
        return web.Response(body=state.body, content_type="application/octet-stream")

    app = web.Application()
    app.router.add_get("/{path:.*}", handler)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    state.url = f"http://images.example:{server.port}"

    async def resolve(self: object, host: str, port: int, family: int) -> list[dict]:
        # Only in this fixture: route the public-looking test host to localhost.
        # The real resolver's address policy is tested separately below.
        assert host == "images.example"
        return [
            {
                "hostname": host,
                "host": "127.0.0.1",
                "port": port,
                "family": socket.AF_INET,
                "proto": socket.IPPROTO_TCP,
                "flags": socket.AI_NUMERICHOST,
            }
        ]

    monkeypatch.setattr(PublicImageResolver, "resolve", resolve)
    try:
        yield state
    finally:
        state.release.set()
        await server.close()


@pytest.mark.parametrize(
    "content, mime, filename",
    [(JPEG, "image/jpeg", "remote.jpg"), (PNG, "image/png", "remote.png")],
)
async def test_remote_image_reaches_addon_as_bytes_without_url_or_credentials(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
    source: ImageSource,
    content: bytes,
    mime: str,
    filename: str,
) -> None:
    source.body = content
    url = source.url + "/image?signature=synthetic%2Ftoken%2Bvalue"
    result = await hass.services.async_call(
        DOMAIN,
        "send_image",
        {"who": "simon", "image_path": url},
        blocking=True,
        return_response=True,
    )
    assert result["status"] == "submitted"
    request = gateway_state.requests[0]
    assert request.image == content
    assert request.image_content_type == mime
    assert request.image_filename == filename
    assert url not in str(request.fields)
    assert "signature" not in str(request.fields)
    assert len(source.requests) == 1
    download = source.requests[0]
    assert download.raw_path.endswith("signature=synthetic%2Ftoken%2Bvalue")
    assert "Authorization" not in download.headers
    assert "Cookie" not in download.headers


async def test_remote_redirect_does_not_keep_cookies(source: ImageSource) -> None:
    image = await async_download_image(source.url + "/redirect")
    assert image.content == JPEG
    assert len(source.requests) == 2
    assert "Cookie" not in source.requests[1].headers


@pytest.mark.parametrize(
    "path, key, hits",
    [
        ("/missing", "image_url_http_error", 1),
        ("/loop", "image_url_redirect_limit", 4),
        ("/private", "image_url_not_allowed", 1),
        ("/large-header", "image_url_too_large", 1),
    ],
)
async def test_remote_download_failure_never_uploads_to_gateway(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
    source: ImageSource,
    path: str,
    key: str,
    hits: int,
) -> None:
    with pytest.raises(HomeAssistantError) as exc:
        await hass.services.async_call(
            DOMAIN,
            "send_image",
            {
                "who": "simon",
                "image_path": source.url + path + "?token=synthetic-secret",
            },
            blocking=True,
        )
    assert exc.value.translation_key == key
    assert "synthetic-secret" not in str(exc.value)
    assert len(source.requests) == hits
    assert gateway_state.requests == []


async def test_stream_limit_applies_without_content_length(source: ImageSource) -> None:
    source.body = JPEG + bytes(MAX_IMAGE_BYTES)
    with pytest.raises(HomeAssistantError) as exc:
        await async_download_image(source.url + "/chunked")
    assert exc.value.translation_key == "image_url_too_large"
    source.body = JPEG + bytes(MAX_IMAGE_BYTES - len(JPEG))
    assert len((await async_download_image(source.url + "/chunked")).content) == MAX_IMAGE_BYTES


@pytest.mark.parametrize("body", [b"<html>Sign in</html>", b"GIF89a", b"RIFFwebp", b""])
async def test_non_image_response_rejected(source: ImageSource, body: bytes) -> None:
    source.body = body
    with pytest.raises(HomeAssistantError) as exc:
        await async_download_image(source.url + "/image")
    assert exc.value.translation_key == "image_url_invalid_format"


async def test_download_timeout_is_bounded(
    source: ImageSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("custom_components.line_oa.remote_images.IMAGE_DOWNLOAD_TIMEOUT", 0.02)
    with pytest.raises(HomeAssistantError) as exc:
        await async_download_image(source.url + "/slow")
    assert exc.value.translation_key == "image_url_timeout"


async def test_unknown_alias_prevents_remote_download(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
    source: ImageSource,
) -> None:
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "send_image",
            {
                "who": "absent",
                "image_path": source.url + "/image",
            },
            blocking=True,
        )
    assert not source.requests
    assert not gateway_state.requests


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://images.example/photo.jpg",
        "https:///missing-host",
        "https://user:pass@images.example/photo.jpg",
        "https://images.example/x#fragment",
        "https://images.example:99999/x",
        "https://images.example/\nimage",
    ],
)
async def test_invalid_url_rejected_before_network(url: str) -> None:
    with pytest.raises(ServiceValidationError) as exc:
        await async_download_image(url)
    assert exc.value.translation_key == "image_url_invalid"


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "169.254.169.254", "192.168.1.1", "[::1]", "[::ffff:127.0.0.1]", "224.0.0.1"],
)
async def test_private_literal_target_rejected_before_network(host: str) -> None:
    with pytest.raises(ServiceValidationError) as exc:
        await async_download_image(f"https://{host}/image")
    assert exc.value.translation_key == "image_url_not_allowed"


async def test_connector_dns_policy_rejects_private_or_mixed_answers() -> None:
    resolver = PublicImageResolver()
    try:
        for addresses in [["8.8.8.8"], ["127.0.0.1"], ["8.8.8.8", "169.254.169.254"]]:
            records = [
                {
                    "hostname": "images.example",
                    "host": ip,
                    "port": 443,
                    "family": socket.AF_INET,
                    "proto": 0,
                    "flags": 0,
                }
                for ip in addresses
            ]
            with patch.object(resolver._resolver, "resolve", AsyncMock(return_value=records)):
                if len(addresses) == 1 and addresses[0] == "8.8.8.8":
                    assert await resolver.resolve("images.example", 443) == records
                else:
                    with pytest.raises(ServiceValidationError):
                        await resolver.resolve("images.example", 443)
    finally:
        await resolver.close()


def test_https_signed_url_keeps_exact_encoding() -> None:
    url = "https://images.example/image?signature=a%2Fb%2Bc&expires=123"
    assert str(_validate_url(url)) == url


async def test_https_download_requires_a_trusted_certificate(
    source: ImageSource, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use a real TLS mock server: reject untrusted TLS, then trust only its test CA."""

    def contexts() -> tuple[ssl.SSLContext, ssl.SSLContext]:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "images.example")])
        now = datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName("images.example")]), critical=False
            )
            .sign(key, hashes.SHA256())
        )
        cert_path = tmp_path / "image-test-cert.pem"
        key_path = tmp_path / "image-test-key.pem"
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        try:
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(cert_path, key_path)
            return server_context, ssl.create_default_context(cafile=str(cert_path))
        finally:
            key_path.unlink(missing_ok=True)
            cert_path.unlink(missing_ok=True)

    server_context, client_context = await asyncio.to_thread(contexts)
    app = web.Application()

    async def image(request: web.Request) -> web.Response:
        assert request.secure
        return web.Response(body=PNG)

    app.router.add_get("/image", image)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server(ssl=server_context)
    url = f"https://images.example:{server.port}/image"
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    def expected_handshake_failure(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        # The negative TLS check makes this test server see the client abort.
        if context.get(
            "message"
        ) == "Error on transport creation for incoming connection" and isinstance(
            context.get("exception"), (ConnectionResetError, ssl.SSLError)
        ):
            return
        if previous_handler:
            previous_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(expected_handshake_failure)
    try:
        with pytest.raises(HomeAssistantError) as exc:
            await async_download_image(url)
        assert exc.value.translation_key == "image_url_download_failed"
        # Equivalent to a publicly trusted CA, scoped to this test connector only.
        from aiohttp import TCPConnector

        monkeypatch.setattr(
            "custom_components.line_oa.remote_images.aiohttp.TCPConnector",
            lambda **kwargs: TCPConnector(ssl=client_context, **kwargs),
        )
        assert (await async_download_image(url)).content == PNG
    finally:
        await server.close()
        loop.set_exception_handler(previous_handler)
