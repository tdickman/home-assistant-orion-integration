"""Switch platform for Orion Sleep."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
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
    """Set up Orion Sleep switch entities."""
    coordinator: OrionDataUpdateCoordinator = entry.runtime_data
    entities: list[OrionScheduleSwitch] = []

    for device in coordinator.devices:
        device_id = device.get("deviceId") or device.get("id")
        if not device_id:
            continue
        entities.append(OrionScheduleSwitch(coordinator, device_id))

    async_add_entities(entities)


class OrionScheduleSwitch(OrionBaseEntity, SwitchEntity):
    """Switch entity for enabling/disabling the sleep schedule."""

    _attr_translation_key = "sleep_schedule"

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_sleep_schedule"

    @property
    def is_on(self) -> bool | None:
        """Return True if the sleep schedule is enabled."""
        schedules = (self.coordinator.data or {}).get("schedules", {})
        return schedules.get("enabled", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the sleep schedule."""
        await self.coordinator.api_client.update_sleep_schedule(
            {"enabled": True}, action="enable"
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the sleep schedule."""
        await self.coordinator.api_client.update_sleep_schedule(
            {"enabled": False}, action="disable"
        )
        await self.coordinator.async_request_refresh()
