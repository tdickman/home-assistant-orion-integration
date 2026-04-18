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
        entities.append(OrionAwayModeSwitch(coordinator, device_id))
        entities.append(OrionScheduleSwitch(coordinator, device_id))

    async_add_entities(entities)


class OrionPowerSwitch(OrionBaseEntity, SwitchEntity):
    """Switch to turn the Orion mattress topper on/off.

    Uses the canonical power primitive `PUT /v1/devices/{id}/live` to
    set all zones on/off in one call. This is distinct from Away Mode,
    which is a presence/schedule override.

    State is derived from each zone's `on` / `is_on` field returned by
    `GET /v1/devices`.
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
        """Return True if the device is on."""
        return self.coordinator.is_device_on(self._device_id)

    def _zone_ids(self) -> list[str]:
        """Return the zone ids for this device."""
        for device in self.coordinator.devices:
            if device.get("id") != self._device_id:
                continue
            return [z.get("id") for z in device.get("zones", []) if z.get("id")]
        return []

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the device via PUT /v1/devices/{id}/live."""
        zone_ids = self._zone_ids()
        if not zone_ids:
            return
        await self.coordinator.api_client.update_live_device_zones(
            device_id=self._device_id,
            zones=[{"id": zid, "on": True} for zid in zone_ids],
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the device via PUT /v1/devices/{id}/live."""
        zone_ids = self._zone_ids()
        if not zone_ids:
            return
        await self.coordinator.api_client.update_live_device_zones(
            device_id=self._device_id,
            zones=[{"id": zid, "on": False} for zid in zone_ids],
        )
        await self.coordinator.async_request_refresh()


class OrionAwayModeSwitch(OrionBaseEntity, SwitchEntity):
    """Switch to control the user's away mode.

    When away mode is ON, the user is marked as away and the device
    stops heating/cooling. Zones lose their user assignment.

    When away mode is OFF, the user is marked as present and the device
    resumes normal operation.

    This is the inverse of the Power switch: Away ON = Power OFF, and
    Away OFF = Power ON. Both are provided so the user can choose the
    mental model that fits their automations best.
    """

    _attr_translation_key = "away_mode"
    _attr_icon = "mdi:home-export-outline"

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_away_mode"

    @property
    def is_on(self) -> bool | None:
        """Return True if the user is away (device is off)."""
        device_on = self.coordinator.is_device_on(self._device_id)
        if device_on is None:
            return None
        return not device_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable away mode (mark user as away, device stops)."""
        await self.coordinator.api_client.set_user_away(
            user_id=self.coordinator.user_id,
            is_away=True,
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable away mode (mark user as present, device resumes)."""
        await self.coordinator.api_client.set_user_away(
            user_id=self.coordinator.user_id,
            is_away=False,
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
