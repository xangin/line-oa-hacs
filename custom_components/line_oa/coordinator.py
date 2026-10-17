"""Status coordinator for the LINE OA integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    GatewayAuthError,
    GatewayConnectionError,
    GatewayStatus,
    LineOAError,
    LineOAGatewayClient,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, LOGGER

type LineOAConfigEntry = ConfigEntry[LineOACoordinator]


class LineOACoordinator(DataUpdateCoordinator[GatewayStatus]):
    """Poll Gateway health and status.

    ``gateway_reachable`` tracks ``GET /api/v1/health`` separately from the
    coordinator data so the "connected" sensor can show ``off`` while the
    LINE session sensors become unavailable.
    """

    config_entry: LineOAConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: LineOAConfigEntry,
        client: LineOAGatewayClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client
        self.gateway_reachable = False

    async def _async_update_data(self) -> GatewayStatus:
        """Fetch health, then status, from the Gateway."""
        try:
            await self.client.async_health()
        except GatewayAuthError as err:
            # Health answered, so the Gateway is up, but it requires a valid key.
            self.gateway_reachable = True
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_api_key"
            ) from err
        except LineOAError as err:
            self.gateway_reachable = False
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="gateway_unreachable",
                translation_placeholders={"error": str(err)},
            ) from err

        self.gateway_reachable = True
        try:
            return await self.client.async_get_status()
        except GatewayAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_api_key"
            ) from err
        except GatewayConnectionError as err:
            self.gateway_reachable = False
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="gateway_unreachable",
                translation_placeholders={"error": str(err)},
            ) from err
        except LineOAError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="status_failed",
                translation_placeholders={"error": str(err)},
            ) from err
