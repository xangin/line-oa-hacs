"""Login reminders use real HA notifications and Repairs with a mock Gateway."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.line_oa.const import (
    CONF_ADDON_SLUG,
    CONF_CONNECTION_MODE,
    DOMAIN,
    MODE_ADDON,
)
from homeassistant.components import persistent_notification as pn
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .mock_gateway import MockGatewayState


def _notifications(hass: HomeAssistant) -> dict:
    return hass.data.get(pn.DOMAIN, {})


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_expiry_reminder_deduplicates_and_clears_on_verified_recovery(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    entry = setup_integration
    alert = entry.runtime_data.login_alert
    gateway_state.session_status = "expired"
    gateway_state.line_logged_in = False
    await _refresh(hass, entry)
    notification = _notifications(hass)[alert.notification_id]
    issue = ir.async_get(hass).async_get_issue(DOMAIN, alert.issue_id)
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.ERROR
    assert entry.data["url"] in notification["message"]
    assert "test-api-key" not in notification["message"]
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []
    for _ in range(3):
        await _refresh(hass, entry)
    assert _notifications(hass)[alert.notification_id] is notification
    assert len(_notifications(hass)) == 1
    # A transient failure does not dismiss the confirmed login problem.
    gateway_state.health_status = 503
    await _refresh(hass, entry)
    assert alert.notification_id in _notifications(hass)
    gateway_state.health_status = 200
    gateway_state.session_status = "unknown"
    gateway_state.line_logged_in = None
    await _refresh(hass, entry)
    assert alert.notification_id in _notifications(hass)
    gateway_state.session_status = "ready"
    gateway_state.line_logged_in = True
    await _refresh(hass, entry)
    assert alert.notification_id not in _notifications(hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, alert.issue_id) is None
    # A later, separate expiry can notify again.
    gateway_state.session_status = "expired"
    gateway_state.line_logged_in = False
    await _refresh(hass, entry)
    assert alert.notification_id in _notifications(hass)


@pytest.mark.parametrize("state", ["auth_required", "disconnected", "error", "unknown"])
async def test_initial_unconfigured_and_transient_states_do_not_prompt_login(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    gateway_state: MockGatewayState,
    state: str,
) -> None:
    gateway_state.session_status = state
    gateway_state.line_logged_in = False
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    alert = config_entry.runtime_data.login_alert
    assert alert.notification_id not in _notifications(hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, alert.issue_id) is None


async def test_expired_on_startup_localized_addon_link_and_unload_cleanup(
    hass: HomeAssistant, config_entry: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    hass.config.language = "zh-Hant"
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry,
        data={
            **config_entry.data,
            CONF_CONNECTION_MODE: MODE_ADDON,
            CONF_ADDON_SLUG: "local_line_oa",
        },
    )
    gateway_state.session_status = "expired"
    gateway_state.line_logged_in = False
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    alert = config_entry.runtime_data.login_alert
    notification = _notifications(hass)[alert.notification_id]
    assert notification["title"] == "LINE OA 需要重新登入"
    assert "/hassio/addon/local_line_oa/info" in notification["message"]
    assert config_entry.data["url"] not in notification["message"]
    assert "不需要重新配對" in notification["message"]
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert alert.notification_id not in _notifications(hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, alert.issue_id) is None


async def test_explicit_logout_is_not_expiry_and_user_dismissal_is_respected(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    entry = setup_integration
    alert = entry.runtime_data.login_alert
    gateway_state.session_status = "auth_required"
    gateway_state.line_logged_in = False
    gateway_state.login_required_reason = "logged_out"
    await _refresh(hass, entry)
    assert alert.notification_id not in _notifications(hass)
    gateway_state.session_status = "expired"
    gateway_state.login_required_reason = "expired"
    await _refresh(hass, entry)
    assert alert.notification_id in _notifications(hass)
    pn.async_dismiss(hass, alert.notification_id)
    await _refresh(hass, entry)
    assert alert.notification_id not in _notifications(hass)
    # The Repairs issue remains until login is restored or the user ignores it.
    assert ir.async_get(hass).async_get_issue(DOMAIN, alert.issue_id) is not None


async def test_legacy_auth_required_after_ready_notifies_without_new_api_field(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    gateway_state.session_status = "auth_required"
    gateway_state.line_logged_in = False
    gateway_state.login_required_reason = None
    await _refresh(hass, setup_integration)
    assert setup_integration.runtime_data.login_alert.notification_id in _notifications(hass)
