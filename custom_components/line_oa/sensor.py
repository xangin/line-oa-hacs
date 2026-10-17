"""Sensors for the LINE OA integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import sanitize_error_text
from .const import LAST_ERROR_NONE, MAX_STATE_LENGTH, SESSION_STATES
from .coordinator import LineOAConfigEntry
from .entity import LineOAEntity

PARALLEL_UPDATES = 0

SESSION_STATUS = SensorEntityDescription(
    key="session_status",
    translation_key="session_status",
    device_class=SensorDeviceClass.ENUM,
    options=SESSION_STATES,
)
LAST_ERROR = SensorEntityDescription(
    key="last_error",
    translation_key="last_error",
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LineOAConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up LINE OA sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            LineOASessionStatusSensor(coordinator, SESSION_STATUS),
            LineOALastErrorSensor(coordinator, LAST_ERROR),
        ]
    )


class LineOAStatusSensor(LineOAEntity, SensorEntity):
    """Base for sensors that need a fresh Gateway status."""

    @property
    def available(self) -> bool:
        """Unavailable whenever the latest status could not be fetched."""
        return super().available and self.coordinator.data is not None


class LineOASessionStatusSensor(LineOAStatusSensor):
    """LINE session state reported by the Gateway."""

    @property
    def native_value(self) -> str:
        """Return the session state, mapping unexpected values to ``unknown``."""
        value = (self.coordinator.data.session_status or "").lower()
        return value if value in SESSION_STATES else "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the official account name, if reported."""
        return {"account_name": self.coordinator.data.account_name}


class LineOALastErrorSensor(LineOAStatusSensor):
    """Most recent Gateway error summary, or ``none``."""

    @property
    def native_value(self) -> str:
        """Return a sanitized, length-limited error summary."""
        last_error = self.coordinator.data.last_error
        if not last_error:
            return LAST_ERROR_NONE
        return sanitize_error_text(last_error, MAX_STATE_LENGTH)
