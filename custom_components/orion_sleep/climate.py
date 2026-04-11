"""Climate platform for Orion Sleep."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
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
    """Set up Orion Sleep climate entities.

    Creates one climate entity per device. The device has zones (zone_a, zone_b)
    but both may be assigned to the same user. We create one entity per device
    since temperature control appears to be device-level via schedules.
    """
    coordinator: OrionDataUpdateCoordinator = entry.runtime_data
    entities: list[OrionClimateEntity] = []

    for device in coordinator.devices:
        device_id = device.get("id")
        if not device_id:
            continue
        entities.append(OrionClimateEntity(coordinator, device_id, device))

    async_add_entities(entities)


class OrionClimateEntity(OrionBaseEntity, ClimateEntity):
    """Climate entity for an Orion Sleep bed.

    Temperature data comes from the sleep schedule (bedtime_temp, wakeup_temp)
    and the latest insights session temperature readings. The API uses Celsius
    internally (temperature_range min=10, max=45).
    """

    _attr_hvac_modes = [HVACMode.HEAT_COOL, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _enable_turn_on_off_backwards_compat = False
    _attr_translation_key = "bed_climate"

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
        device: dict,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._device = device
        self._attr_unique_id = f"{device_id}_climate"

        # Temperature range from device data (Celsius)
        temp_range = device.get("temperature_range", {})
        self._attr_min_temp = float(temp_range.get("min", 10))
        self._attr_max_temp = float(temp_range.get("max", 45))
        self._attr_target_temperature_step = 0.5

    @property
    def current_temperature(self) -> float | None:
        """Return the current bed temperature from the latest session."""
        session = self.coordinator.get_latest_session()
        if not session:
            return None
        temp_data = session.get("temperature", {})
        values = temp_data.get("values", [])
        if values:
            # Return the most recent temperature reading
            return values[-1]
        return None

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature from today's schedule."""
        schedule = self.coordinator.get_today_schedule()
        if not schedule:
            return None
        # Use bedtime_temp as the target
        return schedule.get("bedtime_temp")

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
        schedule = self.coordinator.get_today_schedule()
        if schedule and schedule.get("bedtime_is_active"):
            return HVACMode.HEAT_COOL
        return HVACMode.OFF

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        await self.coordinator.api_client.set_temperature(
            device_id=self._device_id,
            temperature=temp,
        )
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode (limited — schedule-based control)."""
        # The API controls temperature through schedules, not direct on/off.
        # Setting a temperature effectively turns it on.
        _LOGGER.debug("set_hvac_mode called with %s", hvac_mode)
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        """Turn on the climate entity."""
        target = self.target_temperature
        if target is not None:
            await self.coordinator.api_client.set_temperature(
                device_id=self._device_id,
                temperature=target,
            )
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        """Turn off the climate entity."""
        _LOGGER.debug("turn_off called — device is schedule-controlled")
        await self.coordinator.async_request_refresh()
