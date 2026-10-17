"""Mock LINE OA Gateway implementing the HACS API contract.

Used by the automated tests, and runnable standalone for manual testing::

    python tests/mock_gateway.py --port 8787 --api-key test-key

It never contacts LINE. Every send is only recorded in memory.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
import secrets
import time
from typing import Any
import uuid

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
    # (http_status, json_body) returned by message endpoints, or None for success.
    send_response: tuple[int, Any] | None = None
    send_status: str = "submitted"
    send_delay: float = 0.0
    requests: list[RecordedRequest] = field(default_factory=list)
    # Reported API version (``None`` = older LXC Gateway that does not report it).
    api_version: int | None = None
    # --- Gateway app (add-on) pairing, mirroring line-oa-ha-addon pairing.ts ---
    paired: bool = True
    bootstrap: str | None = None
    bootstrap_expires: float = 0.0
    pairing_attempts: list[float] = field(default_factory=list)
    pairing_calls: int = 0
    pairing_status_override: int | None = None

    def discovery_config(self, host: str = "local-line-oa") -> dict[str, Any]:
        """Return the Supervisor discovery config the app would publish."""
        config: dict[str, Any] = {
            "host": host,
            "port": 8787,
            "instance_id": self.instance_id,
            "api_version": 1,
        }
        if self.paired:
            return {**config, "pairing_required": False}
        if self.bootstrap is None or self.bootstrap_expires <= time.time():
            self.bootstrap = secrets.token_urlsafe(32)
            self.bootstrap_expires = time.time() + 600
        expires = datetime.fromtimestamp(self.bootstrap_expires, UTC).isoformat()
        return {
            **config,
            "pairing_required": True,
            "bootstrap_token": self.bootstrap,
            "bootstrap_expires_at": expires,
        }

    def reset_pairing(self) -> None:
        """Simulate "Allow Home Assistant pairing" in the app Web UI."""
        self.paired = False
        self.api_key = "revoked-" + secrets.token_hex(8)
        self.bootstrap = None

    def expire_bootstrap(self) -> None:
        """Make the current bootstrap token expire."""
        self.bootstrap_expires = time.time() - 1


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
        body: dict[str, Any] = {"status": "ok"}
        if state.api_version is not None:
            body["api_version"] = state.api_version
        return web.json_response(body)

    async def pairing_exchange(request: web.Request) -> web.Response:
        state.pairing_calls += 1
        if state.pairing_status_override is not None:
            return web.Response(status=state.pairing_status_override)
        if "Origin" in request.headers or "Cookie" in request.headers:
            return _error(403, "PAIRING_REJECTED", "internal client required")
        now = time.time()
        state.pairing_attempts = [t for t in state.pairing_attempts if now - t < 60]
        if len(state.pairing_attempts) >= 5:
            return _error(429, "PAIRING_RATE_LIMITED", "slow down")
        state.pairing_attempts.append(now)
        body = await request.json()
        try:
            uuid.UUID(str(body.get("request_id")))
        except ValueError:
            return _error(400, "INVALID_PAIRING_REQUEST", "bad request_id")
        if body.get("client") != "homeassistant-line_oa":
            return _error(400, "INVALID_PAIRING_REQUEST", "bad client")
        if (
            state.paired
            or state.bootstrap is None
            or state.bootstrap_expires <= now
            or body.get("instance_id") != state.instance_id
            or body.get("bootstrap_token") != state.bootstrap
        ):
            return _error(401, "PAIRING_REJECTED", "Pairing was rejected")
        state.api_key = "loagw_" + secrets.token_urlsafe(32)
        state.paired = True
        state.bootstrap = None
        return web.json_response(
            {"instance_id": state.instance_id, "api_key": state.api_key, "api_version": 1},
            status=201,
        )

    async def status(request: web.Request) -> web.Response:
        if not authorized(request):
            return _error(401, "UNAUTHORIZED", "Invalid API key")
        if state.status_override is not None:
            code, body = state.status_override
            return web.json_response(body, status=code)
        return web.json_response(
            {
                "instance_id": state.instance_id,
                **({"api_version": state.api_version} if state.api_version is not None else {}),
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
        # The image endpoint carries no text.
        if set(fields) - {"who", "request_id"}:
            return _error(400, "UNSUPPORTED_FIELD", "Only who, image and request_id are accepted")
        return await _send_outcome(fields.get("who", ""), fields.get("request_id", ""))

    app = web.Application(client_max_size=20 * 1024 * 1024)
    app.router.add_get("/api/v1/health", health)
    app.router.add_get("/api/v1/status", status)
    app.router.add_get("/api/v1/aliases", aliases)
    app.router.add_post("/api/v1/messages/text", send_text)
    app.router.add_post("/api/v1/messages/image", send_image)
    app.router.add_post("/api/v1/pairing/exchange", pairing_exchange)
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
