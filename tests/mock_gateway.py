"""Mock LINE OA Gateway implementing the HACS API contract.

Used by the automated tests, and runnable standalone for manual testing::

    python tests/mock_gateway.py --port 8787 --api-key test-key

It never contacts LINE. Every send is only recorded in memory.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web


@dataclass
class RecordedRequest:
    """A message request received by the mock."""

    path: str
    fields: dict[str, Any]
    image: bytes | None = None
    image_content_type: str | None = None
    image_filename: str | None = None


@dataclass
class MockGatewayState:
    """Mutable state and behaviour switches of the mock Gateway."""

    api_key: str = "test-api-key"
    instance_id: str = "11111111-2222-3333-4444-555555555555"
    version: str = "0.1.0"
    aliases: list[str] = field(default_factory=lambda: ["simon", "family"])
    line_logged_in: bool | None = True
    session_status: str = "ready"
    account_name: str = "家庭通知"
    last_error: Any = None
    health_status: int = 200
    status_override: tuple[int, Any] | None = None
    aliases_status: int = 200
    supports_image_message: bool = True
    # (http_status, json_body) returned by message endpoints, or None for success.
    send_response: tuple[int, Any] | None = None
    send_status: str = "submitted"
    send_delay: float = 0.0
    requests: list[RecordedRequest] = field(default_factory=list)


def _error(status: int, code: str, message: str) -> web.Response:
    return web.json_response(
        {"status": "failed", "error": {"code": code, "message": message}}, status=status
    )


def create_app(state: MockGatewayState) -> web.Application:
    """Create the aiohttp application."""

    def authorized(request: web.Request) -> bool:
        return request.headers.get("Authorization") == f"Bearer {state.api_key}"

    async def health(request: web.Request) -> web.Response:
        if state.health_status != 200:
            return web.json_response({"status": "error"}, status=state.health_status)
        return web.json_response({"status": "ok"})

    async def status(request: web.Request) -> web.Response:
        if not authorized(request):
            return _error(401, "UNAUTHORIZED", "Invalid API key")
        if state.status_override is not None:
            code, body = state.status_override
            return web.json_response(body, status=code)
        return web.json_response(
            {
                "instance_id": state.instance_id,
                "version": state.version,
                "gateway_status": "online",
                "line_logged_in": state.line_logged_in,
                "session_status": state.session_status,
                "account_name": state.account_name,
                "last_error": state.last_error,
                "updated_at": "2026-10-17T10:00:00+08:00",
            }
        )

    async def aliases(request: web.Request) -> web.Response:
        if not authorized(request):
            return _error(401, "UNAUTHORIZED", "Invalid API key")
        if state.aliases_status != 200:
            return web.Response(status=state.aliases_status)
        return web.json_response(
            {"aliases": [{"alias": name, "type": "user"} for name in state.aliases]}
        )

    async def _send_outcome(who: str, request_id: str) -> web.Response:
        if state.send_delay:
            await asyncio.sleep(state.send_delay)
        if state.send_response is not None:
            code, body = state.send_response
            if isinstance(body, (bytes, str)):
                return web.Response(status=code, body=body)
            return web.json_response(body, status=code)
        if not state.line_logged_in:
            return _error(409, "SESSION_EXPIRED", "Session expired")
        if who not in state.aliases:
            return _error(404, "ALIAS_NOT_FOUND", "Alias not found")
        return web.json_response(
            {"status": state.send_status, "request_id": request_id, "message_id": "m-1"}
        )

    async def send_text(request: web.Request) -> web.Response:
        if not authorized(request):
            return _error(401, "UNAUTHORIZED", "Invalid API key")
        body = await request.json()
        state.requests.append(RecordedRequest(request.path, dict(body)))
        return await _send_outcome(body.get("who", ""), body.get("request_id", ""))

    async def send_image(request: web.Request) -> web.Response:
        if not authorized(request):
            return _error(401, "UNAUTHORIZED", "Invalid API key")
        reader = await request.multipart()
        fields: dict[str, Any] = {}
        recorded = RecordedRequest(request.path, fields)
        async for part in reader:
            if part.name == "image":
                recorded.image = await part.read()
                recorded.image_content_type = part.headers.get("Content-Type")
                recorded.image_filename = part.filename
            elif part.name:
                fields[part.name] = await part.text()
        state.requests.append(recorded)
        if "message" in fields and not state.supports_image_message:
            return _error(400, "MESSAGE_NOT_SUPPORTED", "message field is not supported")
        return await _send_outcome(fields.get("who", ""), fields.get("request_id", ""))

    app = web.Application(client_max_size=20 * 1024 * 1024)
    app.router.add_get("/api/v1/health", health)
    app.router.add_get("/api/v1/status", status)
    app.router.add_get("/api/v1/aliases", aliases)
    app.router.add_post("/api/v1/messages/text", send_text)
    app.router.add_post("/api/v1/messages/image", send_image)
    return app


def main() -> None:
    """Run the mock Gateway standalone."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--api-key", default="test-api-key")
    args = parser.parse_args()
    state = MockGatewayState(api_key=args.api_key)
    web.run_app(create_app(state), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
