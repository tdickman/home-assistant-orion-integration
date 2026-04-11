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
    entities: list[SwitchEntity] = []

    for device in coordinator.devices:
        device_id = device.get("id")
        if not device_id:
            continue
        entities.append(OrionPowerSwitch(coordinator, device_id))
        entities.append(OrionScheduleSwitch(coordinator, device_id))

    async_add_entities(entities)


class OrionPowerSwitch(OrionBaseEntity, SwitchEntity):
    """Switch to turn the Orion mattress topper on/off.

    This controls the device's active heating/cooling — the same as the
    power button in the Orion app. Under the hood it uses the "user away"
    API: is_away=True turns the device off, is_away=False turns it on.

    The on/off state is detected by checking whether the device's zones
    have a user assigned. When the user is "away" (off), zones lose their
    user field.
    """

    _attr_translation_key = "power"
    _attr_icon = "mdi:power"

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_power"

    @property
    def is_on(self) -> bool | None:
        """Return True if the device is on (user is present, not away)."""
        return self.coordinator.is_device_on(self._device_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the device (set user as present)."""
        await self.coordinator.api_client.set_user_away(
            user_id=self.coordinator.user_id,
            is_away=False,
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the device (set user as away)."""
        await self.coordinator.api_client.set_user_away(
            user_id=self.coordinator.user_id,
            is_away=True,
        )
        await self.coordinator.async_request_refresh()


class OrionScheduleSwitch(OrionBaseEntity, SwitchEntity):
    """Switch entity for sleep schedule active state.

    Real schedule data is keyed by user_id, with each day having
    bedtime_is_active and wakeup_is_active fields. This switch reflects
    whether today's schedule has bedtime_is_active set.
    """

    _attr_translation_key = "sleep_schedule"
    _attr_icon = "mdi:calendar-clock"

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
