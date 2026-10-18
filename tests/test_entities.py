"""Tests for the status coordinator and the four status entities."""

from __future__ import annotations

from datetime import timedelta
import logging
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestServer
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.line_oa.api import GatewayConnectionError
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .mock_gateway import MockGatewayState

CONNECTED = "binary_sensor.line_oa_connected"
LOGGED_IN = "binary_sensor.line_oa_logged_in"
SESSION = "sensor.line_oa_session_status"
LAST_ERROR = "sensor.line_oa_last_error"


def _states(hass: HomeAssistant) -> tuple[str, str, str, str]:
    return tuple(hass.states.get(e).state for e in (CONNECTED, LOGGED_IN, SESSION, LAST_ERROR))


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_healthy_gateway(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    """All four entities report a healthy Gateway and a valid LINE session."""
    assert _states(hass) == ("on", "on", "ready", "none")
    assert hass.states.get(SESSION).attributes["account_name"] == "家庭通知"
    assert hass.states.get(SESSION).attributes["options"] == [
        "ready",
        "auth_required",
        "expired",
        "disconnected",
        "error",
        "unknown",
        "gateway_unreachable",
        "pairing_required",
        "status_unavailable",
        "instance_mismatch",
    ]
    assert hass.states.get(CONNECTED).attributes["device_class"] == "connectivity"


async def test_polls_every_60_seconds(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
) -> None:
    """The coordinator polls on its 60 second interval."""
    gateway_state.session_status = "disconnected"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=50))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(SESSION).state == "ready"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(SESSION).state == "disconnected"


async def test_gateway_offline_and_recovery(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway: TestServer,
    gateway_state: MockGatewayState,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Offline: summary identifies the Add-on failure, then recovers without stale data."""
    gateway_state.health_status = 503
    caplog.set_level(logging.INFO)
    for _ in range(3):
        await _refresh(hass, setup_integration)
    assert _states(hass) == ("off", "unavailable", "gateway_unreachable", "unavailable")
    assert hass.states.get(SESSION).attributes["account_name"] is None
    # Repeated failures are logged once, not on every poll.
    failures = [r for r in caplog.records if "Error fetching line_oa data" in r.getMessage()]
    assert len(failures) == 1
    assert "Cannot reach the LINE OA Gateway" in failures[0].getMessage()
    assert "not sent" not in failures[0].getMessage()

    gateway_state.health_status = 200
    await _refresh(hass, setup_integration)
    assert _states(hass) == ("on", "on", "ready", "none")
    assert any("Fetching line_oa data recovered" in r.getMessage() for r in caplog.records)


async def test_gateway_process_gone(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway: TestServer
) -> None:
    """Connection refused is reported as connected=off."""
    await gateway.close()
    await _refresh(hass, setup_integration)
    assert _states(hass) == ("off", "unavailable", "gateway_unreachable", "unavailable")


async def test_status_error_while_health_ok(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """Gateway answers health but not status: online, but LINE state unknown."""
    gateway_state.status_override = (500, {"error": {"code": "INTERNAL", "message": "boom"}})
    await _refresh(hass, setup_integration)
    assert _states(hass) == ("on", "unavailable", "status_unavailable", "unavailable")


async def test_line_session_expired(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """Expired LINE session: Gateway on, logged in off."""
    gateway_state.line_logged_in = False
    gateway_state.session_status = "expired"
    gateway_state.last_error = "Session expired"
    await _refresh(hass, setup_integration)
    assert _states(hass) == ("on", "off", "expired", "Session expired")


@pytest.mark.parametrize(
    ("session_status", "expected"),
    [
        ("auth_required", "auth_required"),
        ("disconnected", "disconnected"),
        ("error", "error"),
        ("READY", "ready"),
        ("something_new", "unknown"),
        (None, "unknown"),
    ],
)
async def test_session_status_values(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
    session_status: str | None,
    expected: str,
) -> None:
    """Session status maps to the documented enum values only."""
    gateway_state.session_status = session_status  # type: ignore[assignment]
    await _refresh(hass, setup_integration)
    assert hass.states.get(SESSION).state == expected


async def test_logged_in_missing_is_unavailable(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """A status without a boolean line_logged_in is not treated as "off"."""
    gateway_state.line_logged_in = None
    await _refresh(hass, setup_integration)
    assert hass.states.get(LOGGED_IN).state == "unavailable"
    assert hass.states.get(SESSION).state == "unknown"


async def test_ready_without_valid_login_is_not_healthy(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    gateway_state.line_logged_in = False
    await _refresh(hass, setup_integration)
    assert _states(hass) == ("on", "off", "unknown", "none")


async def test_failure_cause_changes_without_successful_poll(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """Consecutive failed polls must update both the summary and connectivity."""
    gateway_state.health_status = 503
    await _refresh(hass, setup_integration)
    assert _states(hass) == ("off", "unavailable", "gateway_unreachable", "unavailable")

    gateway_state.health_status = 200
    gateway_state.status_override = (500, {"error": {"code": "INTERNAL", "message": "boom"}})
    await _refresh(hass, setup_integration)
    assert _states(hass) == ("on", "unavailable", "status_unavailable", "unavailable")

    gateway_state.status_override = (401, {"error": {"code": "INVALID_API_KEY"}})
    await _refresh(hass, setup_integration)
    assert _states(hass) == ("on", "unavailable", "pairing_required", "unavailable")

    gateway_state.status_override = None
    await _refresh(hass, setup_integration)
    assert _states(hass) == ("on", "on", "ready", "none")


async def test_connection_lost_after_health_check(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """A connection failure fetching status must not leave connectivity on."""
    with patch.object(
        setup_integration.runtime_data.client,
        "async_get_status",
        AsyncMock(side_effect=GatewayConnectionError("Connection lost")),
    ):
        await _refresh(hass, setup_integration)
    assert _states(hass) == ("off", "unavailable", "gateway_unreachable", "unavailable")


async def test_health_requires_authentication(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """A protected health endpoint that rejects a key is reachable, needing reauth."""
    gateway_state.health_status = 401
    await _refresh(hass, setup_integration)
    assert _states(hass) == ("on", "unavailable", "pairing_required", "unavailable")


@pytest.mark.parametrize(
    ("last_error", "expected"),
    [
        (None, "none"),
        ("", "none"),
        (
            {"code": "AUTH_REQUIRED", "message": "Authentication required"},
            "Authentication required",
        ),
        ({"code": "LINE_REQUEST_FAILED"}, "LINE_REQUEST_FAILED"),
        ("LINE request failed\nretrying", "LINE request failed retrying"),
    ],
)
async def test_last_error_values(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
    last_error: object,
    expected: str,
) -> None:
    """Last error shows a single-line summary or "none"."""
    gateway_state.last_error = last_error
    await _refresh(hass, setup_integration)
    assert hass.states.get(LAST_ERROR).state == expected


async def test_last_error_redacts_secrets_and_is_length_limited(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """Secrets never reach the entity state and the state fits HA's limit."""
    secret = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"
    gateway_state.last_error = (
        f"LINE request failed Authorization: Bearer {secret} cookie=ses_abcdef123 "
        f"token={secret} " + "x" * 400
    )
    await _refresh(hass, setup_integration)
    state = hass.states.get(LAST_ERROR).state
    assert len(state) <= 255
    assert secret not in state
    assert "ses_abcdef123" not in state
    assert state.startswith("LINE request failed")
    assert "[REDACTED]" in state
