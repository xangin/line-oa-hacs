"""Base entity for the LINE OA integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ENTITY_ID_BASE, DEFAULT_NAME, DOMAIN
from .coordinator import LineOACoordinator


class LineOAEntity(CoordinatorEntity[LineOACoordinator]):
    """Common base for LINE OA status entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: LineOACoordinator, description: EntityDescription) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        # Unique IDs come from the first Gateway's instance_id and stay the same
        # after an explicit switch (LXC <-> app), so entity IDs, names and
        # history are kept. The config entry unique_id follows the current Gateway.
        entry = coordinator.config_entry
        instance_id = entry.data.get(CONF_ENTITY_ID_BASE) or entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{instance_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, instance_id)},
            name=DEFAULT_NAME,
            manufacturer="LINE OA Gateway",
            model="LINE OA Gateway",
            sw_version=coordinator.data.version if coordinator.data else None,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=coordinator.client.base_url,
        )
