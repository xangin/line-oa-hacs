"""Binary sensors for the LINE OA integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import GatewayStatus
from .coordinator import LineOAConfigEntry
from .entity import LineOAEntity

PARALLEL_UPDATES = 0

CONNECTED = BinarySensorEntityDescription(
    key="connected",
    translation_key="connected",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
)
LOGGED_IN = BinarySensorEntityDescription(
    key="logged_in",
    translation_key="logged_in",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LineOAConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up LINE OA binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            LineOAConnectedBinarySensor(coordinator, CONNECTED),
            LineOALoggedInBinarySensor(coordinator, LOGGED_IN),
        ]
    )


class LineOAConnectedBinarySensor(LineOAEntity, BinarySensorEntity):
    """Whether the Gateway HTTP service answers ``/api/v1/health``.

    Always available: when the Gateway cannot be reached the state is ``off``.
    """

    @property
    def available(self) -> bool:
        """Return True; reachability is expressed by the state itself."""
        return True

    @property
    def is_on(self) -> bool:
        """Return True if the Gateway health check succeeded."""
        return self.coordinator.gateway_reachable


class LineOALoggedInBinarySensor(LineOAEntity, BinarySensorEntity):
    """Whether the Gateway reports a valid LINE session.

    Unavailable (not ``off``) when the Gateway status cannot be retrieved.
    """

    @property
    def available(self) -> bool:
        """Return True only with a fresh, valid status."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.coordinator.data.line_logged_in is not None
        )

    @property
    def is_on(self) -> bool | None:
        """Return the Gateway's ``line_logged_in`` value."""
        data: GatewayStatus | None = self.coordinator.data
        return data.line_logged_in if data is not None else None
