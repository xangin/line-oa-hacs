"""Tests for setup, unload, reload and restart."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.line_oa.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component

from .mock_gateway import MockGatewayState

ENTITY_IDS = (
    "binary_sensor.line_oa_connected",
    "binary_sensor.line_oa_logged_in",
    "sensor.line_oa_session_status",
    "sensor.line_oa_last_error",
)


async def test_setup_creates_exactly_four_entities_and_three_actions(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Setup creates the four status entities and the three actions only."""
    assert setup_integration.state is ConfigEntryState.LOADED
    entity_registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(entity_registry, setup_integration.entry_id)
    assert sorted(e.entity_id for e in entries) == sorted(ENTITY_IDS)
    for entity_id in ENTITY_IDS:
        assert hass.states.get(entity_id) is not None
    assert set(hass.services.async_services_for_domain(DOMAIN)) == {
        "send_text",
        "send_image",
        "send_screen_shot",
    }
    assert not hass.services.has_service("notify", "line_oa")
    assert hass.states.async_entity_ids("notify") == []

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), setup_integration.entry_id)
    assert len(devices) == 1
    device = devices[0]
    assert device.identifiers == {(DOMAIN, setup_integration.unique_id)}
    assert device.name == "LINE OA"
    assert device.sw_version == "0.1.0"


async def test_ha_restart_restores_entry_from_storage(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """On HA start a stored entry is set up without any YAML or config flow."""
    config_entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    first_ids = {
        e.unique_id: e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), config_entry.entry_id)
    }

    # Simulate a restart: the entry is torn down and set up again from storage.
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    second_ids = {
        e.unique_id: e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), config_entry.entry_id)
    }
    assert first_ids == second_ids
    assert hass.states.get("binary_sensor.line_oa_connected").state == "on"


async def test_restart_while_gateway_offline_still_creates_entities(
    hass: HomeAssistant, config_entry: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """An offline Gateway at startup does not block setup."""
    gateway_state.health_status = 503
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("binary_sensor.line_oa_connected").state == "off"
    assert hass.states.get("binary_sensor.line_oa_logged_in").state == "unavailable"
    assert hass.states.get("sensor.line_oa_session_status").state == "gateway_unreachable"
    assert hass.states.get("sensor.line_oa_last_error").state == "unavailable"


async def test_integration_reload(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    """Reloading keeps entities and actions working."""
    assert await hass.config_entries.async_reload(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert setup_integration.state is ConfigEntryState.LOADED
    for entity_id in ENTITY_IDS:
        assert hass.states.get(entity_id).state != "unavailable"
    assert hass.services.has_service(DOMAIN, "send_text")


async def test_custom_entity_name_and_id_survive_reload(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """New translations never replace a user's name, ID or unique ID."""
    registry = er.async_get(hass)
    original = registry.async_get("sensor.line_oa_session_status")
    registry.async_update_entity(
        original.entity_id,
        name="我的 LINE 狀態",
        new_entity_id="sensor.my_line_status",
    )
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(setup_integration.entry_id)
    await hass.async_block_till_done()
    updated = registry.async_get("sensor.my_line_status")
    assert updated.unique_id == original.unique_id
    assert updated.name == "我的 LINE 狀態"
    assert hass.states.get(updated.entity_id).attributes["friendly_name"] == "我的 LINE 狀態"
    assert registry.async_get("sensor.line_oa_session_status") is None


async def test_integration_unload(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """Unloading marks entities unavailable; actions then fail clearly."""
    assert await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert setup_integration.state is ConfigEntryState.NOT_LOADED
    for entity_id in ENTITY_IDS:
        assert hass.states.get(entity_id).state == "unavailable"

    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            DOMAIN, "send_text", {"who": "simon", "message": "hi"}, blocking=True
        )
    assert exc_info.value.translation_key == "not_loaded"
    assert gateway_state.requests == []


async def test_invalid_api_key_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """A rejected Gateway API key starts reauth; LINE session state is untouched."""
    gateway_state.api_key = "rotated-key"
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]
    assert hass.states.get("sensor.line_oa_session_status").state == "pairing_required"
    # Gateway itself answered the health check.
    assert hass.states.get("binary_sensor.line_oa_connected").state == "on"
    assert hass.states.get("binary_sensor.line_oa_logged_in").state == "unavailable"


async def test_expired_line_session_does_not_start_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """LINE session problems are fixed in the Gateway UI, never via HA reauth."""
    gateway_state.line_logged_in = False
    gateway_state.session_status = "expired"
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []
    assert hass.states.get("binary_sensor.line_oa_logged_in").state == "off"
