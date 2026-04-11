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

from .const import (
    CONF_TEMP_MODE,
    DEFAULT_TEMP_MODE,
    TEMP_MODE_OFFSET,
    TEMP_OFFSET_MIDPOINT,
)
from .coordinator import OrionDataUpdateCoordinator
from .entity import OrionBaseEntity

_LOGGER = logging.getLogger(__name__)


def _abs_to_offset(celsius: float | None) -> float | None:
    """Convert absolute Celsius to relative offset from midpoint."""
    if celsius is None:
        return None
    return round(celsius - TEMP_OFFSET_MIDPOINT, 1)


def _offset_to_abs(offset: float) -> float:
    """Convert relative offset to absolute Celsius."""
    return round(offset + TEMP_OFFSET_MIDPOINT, 1)


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
    and the latest insights session temperature readings.

    Supports two display modes (configurable in options):
    - Offset mode (default): Shows relative offset like the Orion app (-10 to +10).
      The offset is relative to a midpoint of 27.5°C. This matches the app's UI
      where users see values like "-3", "0", "+5".
    - Absolute mode: Shows raw Celsius values (10°C to 45°C) as received from
      the API. Useful for users who want exact temperatures.

    Internally, the API always uses absolute Celsius. Conversion happens at the
    presentation layer only.
    """

    _attr_hvac_modes = [HVACMode.HEAT_COOL, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
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

    @property
    def _use_offset_mode(self) -> bool:
        """Return True if the user chose offset (app-style) temperature display."""
        mode = self.coordinator.config_entry.options.get(
            CONF_TEMP_MODE, DEFAULT_TEMP_MODE
        )
        return mode == TEMP_MODE_OFFSET

    @property
    def temperature_unit(self) -> str:
        """Return the temperature unit.

        In offset mode we return CELSIUS but the values are offsets.
        HA doesn't have a native "offset" unit, so we use CELSIUS and
        adjust the display range and step accordingly.
        """
        return UnitOfTemperature.CELSIUS

    @property
    def min_temp(self) -> float:
        """Return minimum temperature."""
        temp_range = self._device.get("temperature_range", {})
        abs_min = float(temp_range.get("min", 10))
        if self._use_offset_mode:
            return _abs_to_offset(abs_min) or -17.5
        return abs_min

    @property
    def max_temp(self) -> float:
        """Return maximum temperature."""
        temp_range = self._device.get("temperature_range", {})
        abs_max = float(temp_range.get("max", 45))
        if self._use_offset_mode:
            return _abs_to_offset(abs_max) or 17.5
        return abs_max

    @property
    def target_temperature_step(self) -> float:
        """Return the step for temperature adjustments."""
        if self._use_offset_mode:
            return 1.0
        return 0.5

    @property
    def current_temperature(self) -> float | None:
        """Return the current bed temperature from the latest session."""
        session = self.coordinator.get_latest_session()
        if not session:
            return None
        temp_data = session.get("temperature", {})
        values = temp_data.get("values", [])
        if values:
            abs_temp = values[-1]
            if self._use_offset_mode:
                return _abs_to_offset(abs_temp)
            return abs_temp
        return None

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature from today's schedule."""
        schedule = self.coordinator.get_today_schedule()
        if not schedule:
            return None
        abs_temp = schedule.get("bedtime_temp")
        if abs_temp is None:
            return None
        if self._use_offset_mode:
            return _abs_to_offset(abs_temp)
        return abs_temp

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
        schedule = self.coordinator.get_today_schedule()
        if schedule and schedule.get("bedtime_is_active"):
            return HVACMode.HEAT_COOL
        return HVACMode.OFF

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes showing the temperature mode and schedule temps."""
        attrs: dict[str, Any] = {
            "temperature_mode": "offset" if self._use_offset_mode else "absolute",
        }
        schedule = self.coordinator.get_today_schedule()
        if schedule:
            if self._use_offset_mode:
                for key in (
                    "bedtime_temp",
                    "wakeup_temp",
                    "phase_1_temp",
                    "phase_2_temp",
                ):
                    val = schedule.get(key)
                    if val is not None:
                        attrs[key] = _abs_to_offset(val)
            else:
                for key in (
                    "bedtime_temp",
                    "wakeup_temp",
                    "phase_1_temp",
                    "phase_2_temp",
                ):
                    val = schedule.get(key)
                    if val is not None:
                        attrs[key] = val
        return attrs

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        # Convert back to absolute Celsius for the API
        if self._use_offset_mode:
            temp = _offset_to_abs(temp)
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
            # Convert back to absolute if in offset mode
            if self._use_offset_mode:
                target = _offset_to_abs(target)
            await self.coordinator.api_client.set_temperature(
                device_id=self._device_id,
                temperature=target,
            )
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        """Turn off the climate entity."""
        _LOGGER.debug("turn_off called — device is schedule-controlled")
        await self.coordinator.async_request_refresh()
