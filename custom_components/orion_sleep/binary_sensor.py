"""Binary sensor platform for Orion Sleep."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import OrionDataUpdateCoordinator
from .entity import OrionBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Orion Sleep binary sensor entities."""
    coordinator: OrionDataUpdateCoordinator = entry.runtime_data
    entities: list[OrionSessionActiveBinarySensor] = []

    for device in coordinator.devices:
        device_id = device.get("id")
        if not device_id:
            continue
        entities.append(OrionSessionActiveBinarySensor(coordinator, device_id))

    async_add_entities(entities)


class OrionSessionActiveBinarySensor(OrionBaseEntity, BinarySensorEntity):
    """Binary sensor indicating if a sleep session is active.

    Determined by checking if the latest session in insights has
    is_in_progress == True.

    We intentionally do NOT set a device_class here. Using
    BinarySensorDeviceClass.RUNNING shows "Running / Not running" which
    is confusing for sleep tracking. Instead we rely on translation_key
    to provide "Asleep / Not asleep" state labels.
    """

    _attr_translation_key = "sleep_session_active"
    _attr_icon = "mdi:bed"

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_session_active"

    @property
    def is_on(self) -> bool | None:
        """Return True if a sleep session is currently active."""
        session = self.coordinator.get_latest_session()
        if not session:
            return False
        return session.get("is_in_progress", False)
