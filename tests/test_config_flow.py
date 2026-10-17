"""Config flow tests: add-on-only setup and the experimental external Gateway.

Setting up LINE OA always uses the LINE OA add-on (see test_addon.py). An
external Gateway is an experimental option available only in Reconfigure of an
existing entry; its validation is tested here through that path.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestServer
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.line_oa.const import DOMAIN
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .mock_gateway import MockGatewayState, create_app

MANIFEST = Path(__file__).parent.parent / "custom_components" / "line_oa" / "manifest.json"


async def _open_external(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    """Open Reconfigure; without Supervisor it goes straight to the external form."""
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure_external"
    return result


async def test_add_integration_requires_addon_supervisor(hass: HomeAssistant) -> None:
    """Without Supervisor there is no setup path (the add-on is required)."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "addon_not_supported"


async def test_add_integration_never_offers_external(hass: HomeAssistant) -> None:
    """The add flow has no connection-mode menu and no external form."""
    with (
        patch("custom_components.line_oa.config_flow.is_hassio", return_value=True),
        patch("custom_components.line_oa.config_flow.async_find_discovery", return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "addon_not_found"


def test_no_external_discovery_in_manifest() -> None:
    """External Gateways are not auto-discovered (no zeroconf card)."""
    manifest = json.loads(MANIFEST.read_text())
    assert "zeroconf" not in manifest


async def test_duplicate_gateway_is_rejected(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Only one active Gateway entry is allowed."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


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
async def test_external_invalid_url(
    hass: HomeAssistant, setup_integration: MockConfigEntry, url: str
) -> None:
    result = await _open_external(hass, setup_integration)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: url, CONF_API_KEY: "key"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_URL: "invalid_url"}


async def test_external_health_error_is_cannot_connect(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_url: str,
    gateway_state: MockGatewayState,
) -> None:
    result = await _open_external(hass, setup_integration)
    gateway_state.health_status = 500
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: gateway_url}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_external_invalid_api_key(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_url: str
) -> None:
    result = await _open_external(hass, setup_integration)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: gateway_url, CONF_API_KEY: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}


async def test_external_missing_instance_id(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_url: str,
    gateway_state: MockGatewayState,
) -> None:
    result = await _open_external(hass, setup_integration)
    gateway_state.status_override = (200, {"gateway_status": "online"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: gateway_url}
    )
    assert result["errors"] == {"base": "invalid_response"}


async def test_external_unexpected_exception(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_url: str
) -> None:
    result = await _open_external(hass, setup_integration)
    with patch("custom_components.line_oa.config_flow.validate_gateway", side_effect=RuntimeError):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: gateway_url}
        )
    assert result["errors"] == {"base": "unknown"}


async def test_external_expired_line_session_is_not_a_config_error(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_url: str,
    gateway_state: MockGatewayState,
) -> None:
    gateway_state.line_logged_in = False
    gateway_state.session_status = "expired"
    result = await _open_external(hass, setup_integration)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: gateway_url}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert hass.states.get("binary_sensor.line_oa_logged_in").state == "off"


@pytest.mark.parametrize(("version", "error"), [(None, None), (1, None), (2, "incompatible_api")])
async def test_external_gateway_api_version(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_url: str,
    gateway_state: MockGatewayState,
    version: int | None,
    error: str | None,
) -> None:
    """Older LXC Gateways (no api_version) and v1 work; other versions are refused."""
    gateway_state.api_version = version
    result = await _open_external(hass, setup_integration)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: gateway_url}
    )
    await hass.async_block_till_done()
    if error is None:
        assert result["reason"] == "reconfigure_successful"
    else:
        assert result["errors"] == {"base": error}


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
    assert result["step_id"] == "reconfigure_external"

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
