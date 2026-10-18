"""Status coordinator for the LINE OA integration."""

from __future__ import annotations

from asyncio import Lock

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    GatewayAuthError,
    GatewayConnectionError,
    GatewayStatus,
    LineOAError,
    LineOAGatewayClient,
)
from .const import CONF_INSTANCE_ID, DEFAULT_SCAN_INTERVAL, DOMAIN, LINE_SESSION_STATES, LOGGER
from .login_alert import LineLoginAlert

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
        self._status_problem: str | None = None
        self._previous_failure: tuple[bool, str] | None = None
        self.login_alert = LineLoginAlert(hass, config_entry)
        self.remote_image_lock = Lock()

    @property
    def service_status(self) -> str:
        """Describe the failing connection layer without using stale LINE data."""
        if self._status_problem is not None:
            return self._status_problem
        if not self.last_update_success or self.data is None:
            return "unknown"
        value = (self.data.session_status or "").lower()
        if value not in LINE_SESSION_STATES:
            return "unknown"
        if value == "ready" and self.data.line_logged_in is not True:
            return "unknown"
        return value

    @callback
    def _async_refresh_finished(self) -> None:
        """Notify entities if one failure changes to another between failed polls."""
        super()._async_refresh_finished()
        failure = (
            None if self.last_update_success else (self.gateway_reachable, self.service_status)
        )
        # The coordinator normally skips listeners on consecutive failures.
        # Their cause can change (e.g. stopped app -> invalid pairing).
        if (
            self._previous_failure is not None
            and failure is not None
            and failure != self._previous_failure
        ):
            self.async_update_listeners()
        self._previous_failure = failure

    async def _async_update_data(self) -> GatewayStatus:
        """Fetch health, then status, from the Gateway."""
        self._status_problem = "unknown"
        try:
            await self.client.async_health()
        except GatewayAuthError as err:
            # Health answered, so the Gateway is up, but it requires a valid key.
            self.gateway_reachable = True
            self._status_problem = "pairing_required"
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_api_key"
            ) from err
        except LineOAError as err:
            self.gateway_reachable = False
            self._status_problem = "gateway_unreachable"
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="gateway_unreachable",
                translation_placeholders={"error": str(err)},
            ) from err

        self.gateway_reachable = True
        try:
            status = await self.client.async_get_status()
        except GatewayAuthError as err:
            self._status_problem = "pairing_required"
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_api_key"
            ) from err
        except GatewayConnectionError as err:
            self.gateway_reachable = False
            self._status_problem = "gateway_unreachable"
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="gateway_unreachable",
                translation_placeholders={"error": str(err)},
            ) from err
        except LineOAError as err:
            self._status_problem = "status_unavailable"
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="status_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        expected = self.config_entry.data.get(CONF_INSTANCE_ID) or self.config_entry.unique_id
        if expected and status.instance_id != expected:
            # A different Gateway now answers at this address (e.g. reinstalled).
            # Do not show its state as ours; the user must reconfigure explicitly.
            self._status_problem = "instance_mismatch"
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="instance_mismatch")
        await self.login_alert.async_update(status)
        self._status_problem = None
        return status
