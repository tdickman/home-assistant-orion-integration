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
        device_id = device.get("id")
        if not device_id:
            continue
        entities.append(OrionScheduleSwitch(coordinator, device_id))

    async_add_entities(entities)


class OrionScheduleSwitch(OrionBaseEntity, SwitchEntity):
    """Switch entity for sleep schedule active state.

    Real schedule data is keyed by user_id, with each day having
    bedtime_is_active and wakeup_is_active fields. This switch reflects
    whether today's schedule has bedtime_is_active set.
    """

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
        """Return True if today's sleep schedule is active."""
        schedule = self.coordinator.get_today_schedule()
        if not schedule:
            return None
        return schedule.get("bedtime_is_active", False)

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
