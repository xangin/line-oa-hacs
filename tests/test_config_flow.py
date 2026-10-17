"""Tests for the LINE OA config, reconfigure and reauth flows."""

from __future__ import annotations

from unittest.mock import patch

from aiohttp.test_utils import TestServer
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.line_oa.const import DOMAIN
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, UnknownFlow

from .mock_gateway import MockGatewayState, create_app


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


async def test_user_flow_creates_entry(
    hass: HomeAssistant, gateway_url: str, gateway_state: MockGatewayState
) -> None:
    """Health, API key, status and instance_id are checked; then an entry is created."""
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: gateway_url + "/", CONF_API_KEY: gateway_state.api_key}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "LINE OA"
    assert result["data"] == {CONF_URL: gateway_url, CONF_API_KEY: gateway_state.api_key}
    assert result["result"].unique_id == gateway_state.instance_id
    assert hass.states.get("binary_sensor.line_oa_connected").state == "on"


async def test_user_flow_with_expired_line_session_still_succeeds(
    hass: HomeAssistant, gateway_url: str, gateway_state: MockGatewayState
) -> None:
    """An expired LINE session is not a Gateway configuration error."""
    gateway_state.line_logged_in = False
    gateway_state.session_status = "expired"
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: gateway_url, CONF_API_KEY: gateway_state.api_key}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert hass.states.get("binary_sensor.line_oa_logged_in").state == "off"
    assert hass.states.get("sensor.line_oa_session_status").state == "expired"


@pytest.mark.parametrize(
    "url",
    [
        "192.168.1.50:8787",
        "ftp://192.168.1.50",
        "http://",
        "http://user:pass@192.168.1.50:8787",
        "http://192.168.1.50:8787/?x=1",
    ],
)
async def test_user_flow_invalid_url(hass: HomeAssistant, url: str) -> None:
    """Malformed URLs are rejected before any request."""
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: url, CONF_API_KEY: "key"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_URL: "invalid_url"}


async def test_user_flow_cannot_connect_then_recover(
    hass: HomeAssistant, gateway_url: str, gateway_state: MockGatewayState
) -> None:
    """An unreachable Gateway shows cannot_connect; the user can retry."""
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: "http://127.0.0.1:9", CONF_API_KEY: gateway_state.api_key}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: gateway_url, CONF_API_KEY: gateway_state.api_key}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_health_error_is_cannot_connect(
    hass: HomeAssistant, gateway_url: str, gateway_state: MockGatewayState
) -> None:
    """A failing health endpoint is reported as cannot_connect."""
    gateway_state.health_status = 500
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: gateway_url, CONF_API_KEY: gateway_state.api_key}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_invalid_api_key(hass: HomeAssistant, gateway_url: str) -> None:
    """A wrong API key is reported as invalid_auth."""
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: gateway_url, CONF_API_KEY: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_missing_instance_id(
    hass: HomeAssistant, gateway_url: str, gateway_state: MockGatewayState
) -> None:
    """A status without instance_id cannot be used as a stable unique ID."""
    gateway_state.status_override = (200, {"gateway_status": "online"})
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: gateway_url, CONF_API_KEY: gateway_state.api_key}
    )
    assert result["errors"] == {"base": "invalid_response"}


async def test_user_flow_unexpected_exception(
    hass: HomeAssistant, gateway_url: str, gateway_state: MockGatewayState
) -> None:
    """Unexpected errors are reported as unknown."""
    flow_id = await _start(hass)
    with patch("custom_components.line_oa.config_flow.validate_gateway", side_effect=RuntimeError):
        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_URL: gateway_url, CONF_API_KEY: gateway_state.api_key}
        )
    assert result["errors"] == {"base": "unknown"}


async def test_duplicate_gateway_is_rejected(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Only one Gateway entry is allowed (single_config_entry)."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_same_gateway_in_parallel_flow_is_already_configured(
    hass: HomeAssistant, gateway_url: str, gateway_state: MockGatewayState
) -> None:
    """The same Gateway (identified by instance_id, not IP) cannot be added twice."""
    first = await _start(hass)
    second = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        first, {CONF_URL: gateway_url, CONF_API_KEY: gateway_state.api_key}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY

    # HA aborts the other in-progress flow as soon as the single entry exists.
    with pytest.raises(UnknownFlow):
        await hass.config_entries.flow.async_configure(
            second, {CONF_URL: gateway_url + "/", CONF_API_KEY: gateway_state.api_key}
        )
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_unique_id_check_uses_instance_id(
    hass: HomeAssistant, gateway_url: str, gateway_state: MockGatewayState
) -> None:
    """The unique-ID check aborts with already_configured for the same instance_id.

    single_config_entry normally stops a second flow earlier; this exercises the
    instance_id check itself by bypassing that guard.
    """
    MockConfigEntry(domain=DOMAIN, unique_id=gateway_state.instance_id, data={}).add_to_hass(hass)
    with patch("homeassistant.loader.Integration.single_config_entry", False):
        flow_id = await _start(hass)
        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_URL: gateway_url, CONF_API_KEY: gateway_state.api_key}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_changes_url_and_api_key(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
) -> None:
    """Reconfigure re-validates and updates URL and API key in place."""
    # The same Gateway instance, now reachable on a different address/port.
    moved = TestServer(create_app(gateway_state), host="127.0.0.1")
    await moved.start_server()
    new_url = str(moved.make_url("")).rstrip("/")
    gateway_state.api_key = "new-key"
    result = await setup_integration.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: new_url})
    # Old key is kept when the field is left empty -> rejected by the Gateway.
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: new_url, CONF_API_KEY: "new-key"},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert setup_integration.data[CONF_URL] == new_url
    assert setup_integration.data[CONF_API_KEY] == "new-key"
    assert hass.states.get("binary_sensor.line_oa_connected").state == "on"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    await hass.config_entries.async_unload(setup_integration.entry_id)
    await moved.close()


async def test_reconfigure_keeps_api_key_when_empty(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_url: str,
    gateway_state: MockGatewayState,
) -> None:
    """Leaving the API key empty keeps the stored key."""
    result = await setup_integration.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: gateway_url}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert setup_integration.data[CONF_API_KEY] == gateway_state.api_key


async def test_reconfigure_to_different_gateway_is_rejected(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_url: str,
    gateway_state: MockGatewayState,
) -> None:
    """Reconfigure cannot silently swap in a different Gateway instance."""
    gateway_state.instance_id = "another-gateway"
    result = await setup_integration.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: gateway_url}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_gateway"


async def test_reconfigure_cannot_connect(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Reconfigure shows an error for an unreachable URL."""
    result = await setup_integration.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "http://127.0.0.1:9"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth_flow(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
) -> None:
    """Reauth asks only for a new Gateway API key."""
    gateway_state.api_key = "rotated"
    result = await setup_integration.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "still-wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "rotated"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert setup_integration.data[CONF_API_KEY] == "rotated"
