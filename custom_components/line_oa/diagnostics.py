"""Diagnostics for LINE OA (secrets redacted)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from .const import CONF_CONNECTION_MODE, MODE_EXTERNAL
from .coordinator import LineOAConfigEntry

TO_REDACT = {CONF_API_KEY, "bootstrap_token"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LineOAConfigEntry
) -> dict[str, Any]:
    """Return diagnostics; API keys (current and previous) are never included."""
    coordinator = entry.runtime_data
    status = coordinator.data
    return {
        "connection_mode": entry.data.get(CONF_CONNECTION_MODE, MODE_EXTERNAL),
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "gateway_reachable": coordinator.gateway_reachable,
        "last_update_success": coordinator.last_update_success,
        "status": None
        if status is None
        else {
            "instance_id": status.instance_id,
            "api_version": status.api_version,
            "version": status.version,
            "line_logged_in": status.line_logged_in,
            "session_status": status.session_status,
            "last_error": status.last_error,
        },
    }
