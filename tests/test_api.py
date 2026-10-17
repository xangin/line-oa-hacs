"""Unit tests for the Gateway API client (no Home Assistant instance needed)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import logging
from unittest.mock import patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from custom_components.line_oa.api import (
    AliasNotFoundError,
    GatewayAuthError,
    GatewayConnectionError,
    GatewayResponseError,
    InvalidRequestError,
    LineOAGatewayClient,
    LineSessionError,
    RateLimitedError,
    SendStatus,
    SendUnknownError,
    normalize_base_url,
    sanitize_error_text,
)

API_KEY = "super-secret-api-key-0123456789"


class Counter:
    """Counts requests per path."""

    def __init__(self) -> None:
        self.hits: dict[str, int] = {}

    def hit(self, request: web.Request) -> None:
        self.hits[request.path] = self.hits.get(request.path, 0) + 1


@pytest.fixture
def counter() -> Counter:
    return Counter()


@pytest.fixture
async def server(counter: Counter, socket_enabled: None) -> AsyncGenerator[TestServer]:
    async def drop_connection(request: web.Request) -> web.StreamResponse:
        counter.hit(request)
        await request.read()
        assert request.transport is not None
        request.transport.close()
        await asyncio.sleep(0.1)
        return web.Response()

    async def redirect(request: web.Request) -> web.StreamResponse:
        counter.hit(request)
        raise web.HTTPTemporaryRedirect("/api/v1/redirected")

    async def redirected(request: web.Request) -> web.StreamResponse:
        counter.hit(request)
        return web.json_response({"status": "submitted"})

    async def slow_get(request: web.Request) -> web.StreamResponse:
        counter.hit(request)
        await asyncio.sleep(1)
        return web.json_response({})

    async def aliases(request: web.Request) -> web.StreamResponse:
        return web.json_response(["simon", {"alias": "family"}, {"alias": ""}, 3])

    async def status_401(request: web.Request) -> web.StreamResponse:
        return web.json_response({"error": "UNAUTHORIZED"}, status=401)

    async def error_body(request: web.Request) -> web.StreamResponse:
        kind = request.query.get("kind")
        bodies = {
            "nested": ({"error": {"code": "alias_not_found", "message": "x"}}, 404),
            "flat": ({"code": "SESSION_EXPIRED", "message": "Session expired"}, 409),
            "string": ({"error": "RATE_LIMITED"}, 429),
            "text": ({"error": "something odd happened"}, 422),
        }
        body, status = bodies[kind or "nested"]
        return web.json_response(body, status=status, headers={"Retry-After": "30"})

    app = web.Application()
    app.router.add_post("/api/v1/messages/text", drop_connection)
    app.router.add_post("/redirect/api/v1/messages/text", redirect)
    app.router.add_post("/api/v1/redirected", redirected)
    app.router.add_get("/slow/api/v1/health", slow_get)
    app.router.add_get("/api/v1/aliases", aliases)
    app.router.add_get("/api/v1/status", status_401)
    app.router.add_post("/errors/api/v1/messages/text", error_body)
    srv = TestServer(app, host="127.0.0.1")
    await srv.start_server()
    yield srv
    await srv.close()


@pytest.fixture
async def session() -> AsyncGenerator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as client_session:
        yield client_session


def _url(server: TestServer, prefix: str = "") -> str:
    return str(server.make_url(prefix)).rstrip("/")


async def test_post_connection_drop_is_unknown_and_not_retried(
    server: TestServer, session: aiohttp.ClientSession, counter: Counter
) -> None:
    """A dropped connection after sending a POST is "unknown"; no retry."""
    client = LineOAGatewayClient(session, _url(server), API_KEY)
    # Prime a keep-alive connection so aiohttp's stale-connection retry would apply
    # if POST were treated as idempotent.
    await client.async_get_aliases()
    with pytest.raises(SendUnknownError) as exc_info:
        await client.async_send_text("simon", "hi")
    assert counter.hits["/api/v1/messages/text"] == 1
    assert exc_info.value.request_id


async def test_post_redirect_is_not_followed(
    server: TestServer, session: aiohttp.ClientSession, counter: Counter
) -> None:
    """Redirects would re-submit the body, so they are never followed."""
    client = LineOAGatewayClient(session, _url(server, "/redirect"), API_KEY)
    with pytest.raises(InvalidRequestError):
        await client.async_send_text("simon", "hi")
    assert "/api/v1/redirected" not in counter.hits


async def test_connection_refused_is_not_sent(socket_enabled: None) -> None:
    """Connection refused means the request was not delivered."""
    async with aiohttp.ClientSession() as client_session:
        client = LineOAGatewayClient(client_session, "http://127.0.0.1:9", API_KEY)
        with pytest.raises(GatewayConnectionError):
            await client.async_send_text("simon", "hi")
        with pytest.raises(GatewayConnectionError):
            await client.async_health()


async def test_get_timeout_is_connection_error(
    server: TestServer, session: aiohttp.ClientSession
) -> None:
    """GET timeouts are treated as an unreachable Gateway."""
    client = LineOAGatewayClient(session, _url(server, "/slow"), API_KEY)
    with (
        patch("custom_components.line_oa.api.GET_TIMEOUT", 0.2),
        pytest.raises(GatewayConnectionError),
    ):
        await client.async_health()


async def test_aliases_formats(server: TestServer, session: aiohttp.ClientSession) -> None:
    """Aliases accept strings or objects with an alias key."""
    client = LineOAGatewayClient(session, _url(server), API_KEY)
    assert await client.async_get_aliases() == ["simon", "family"]


async def test_auth_error_never_contains_api_key(
    server: TestServer, session: aiohttp.ClientSession, caplog: pytest.LogCaptureFixture
) -> None:
    """The API key is not part of errors or logs."""
    caplog.set_level(logging.DEBUG)
    client = LineOAGatewayClient(session, _url(server), API_KEY)
    with pytest.raises(GatewayAuthError) as exc_info:
        await client.async_get_status()
    assert API_KEY not in str(exc_info.value)
    assert API_KEY not in repr(exc_info.value.args)
    assert API_KEY not in caplog.text


@pytest.mark.parametrize(
    ("kind", "error"),
    [
        ("nested", AliasNotFoundError),
        ("flat", LineSessionError),
        ("string", RateLimitedError),
        ("text", InvalidRequestError),
    ],
)
async def test_error_body_formats(
    server: TestServer, session: aiohttp.ClientSession, kind: str, error: type[Exception]
) -> None:
    """Gateway error codes are read from the supported body shapes."""
    client = LineOAGatewayClient(session, _url(server, "/errors"), API_KEY)
    with (
        patch(
            "custom_components.line_oa.api.API_MESSAGES_TEXT", f"/api/v1/messages/text?kind={kind}"
        ),
        pytest.raises(error) as exc_info,
    ):
        await client.async_send_text("simon", "hi")
    if isinstance(exc_info.value, RateLimitedError):
        assert exc_info.value.retry_after == "30"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://192.168.1.50:8787", "http://192.168.1.50:8787"),
        ("http://192.168.1.50:8787/", "http://192.168.1.50:8787"),
        ("  https://gw.example.com/line/ ", "https://gw.example.com/line"),
    ],
)
def test_normalize_base_url(url: str, expected: str) -> None:
    assert normalize_base_url(url) == expected


@pytest.mark.parametrize(
    "url", ["192.168.1.50", "ftp://x", "http://u:p@x", "http://x/?a=1", "http://x/#f"]
)
def test_normalize_base_url_rejects(url: str) -> None:
    with pytest.raises(ValueError):
        normalize_base_url(url)


def test_sanitize_error_text() -> None:
    token = "A" * 40
    text = sanitize_error_text(
        f"failed\r\nAuthorization: Bearer {token}; Cookie: sid=abc; password=hunter2 api_key='k'"
    )
    assert token not in text
    assert "hunter2" not in text
    assert "sid=abc" not in text
    assert "'k'" not in text
    assert "\n" not in text
    long_text = sanitize_error_text("LINE request failed " * 100)
    assert len(long_text) == 255
    assert long_text.endswith("…")
    # Long opaque strings are treated as possible secrets.
    assert sanitize_error_text("x" * 1000) == "[REDACTED]"


def test_status_enum_values() -> None:
    assert [s.value for s in SendStatus] == ["submitted", "confirmed", "failed", "unknown"]


def test_status_response_requires_instance_id() -> None:
    from custom_components.line_oa.api import GatewayStatus

    with pytest.raises(GatewayResponseError):
        GatewayStatus.from_dict({"instance_id": "  "})
    with pytest.raises(GatewayResponseError):
        GatewayStatus.from_dict([])
    status = GatewayStatus.from_dict({"instance_id": "id", "line_logged_in": "yes"})
    assert status.line_logged_in is None
