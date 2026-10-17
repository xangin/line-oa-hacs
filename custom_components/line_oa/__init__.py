"""The LINE OA integration.

Sends LINE Official Account notifications through an external LINE OA Gateway.
This integration never talks to LINE directly; it only needs the Gateway URL
and a Gateway API key.
"""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY, CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import LineOAGatewayClient
from .const import DOMAIN
from .coordinator import LineOAConfigEntry, LineOACoordinator
from .services import async_setup_services

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions (once, independent of entries)."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LineOAConfigEntry) -> bool:
    """Set up LINE OA from a config entry."""
    client = LineOAGatewayClient(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_API_KEY]
    )
    coordinator = LineOACoordinator(hass, entry, client)
    # Do not block setup on an offline Gateway: the entities must exist so the
    # "connected" sensor can report "off" and recover automatically.
    await coordinator.async_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LineOAConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
