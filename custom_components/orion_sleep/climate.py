"""Climate platform for Orion Sleep."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ZONE_LEFT, DEFAULT_ZONE_LEFT
from .coordinator import OrionDataUpdateCoordinator
from .entity import OrionBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Orion Sleep climate entities — one per zone per device."""
    coordinator: OrionDataUpdateCoordinator = entry.runtime_data

    zone_left = entry.options.get(CONF_ZONE_LEFT, DEFAULT_ZONE_LEFT)
    zone_right = "zone_b" if zone_left == "zone_a" else "zone_a"

    entities: list[OrionZoneClimate] = []
    for device in coordinator.devices:
        device_id = device.get("id")
        if not device_id:
            continue
        entities.append(
            OrionZoneClimate(coordinator, device_id, device, zone_left, "left")
        )
        entities.append(
            OrionZoneClimate(coordinator, device_id, device, zone_right, "right")
        )

    async_add_entities(entities)


class OrionZoneClimate(OrionBaseEntity, ClimateEntity):
    """Climate entity for a single Orion Sleep zone (zone_a or zone_b).

    Current temperature comes from insights sessions filtered by zone_id.
    Target temperature and on/off state come from the per-zone live snapshot.
    set_temperature and hvac_mode changes call the per-zone PUT endpoint.
    """

    _attr_hvac_modes = [HVACMode.HEAT_COOL, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _enable_turn_on_off_backwards_compat = False

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
        device: dict,
        zone_id: str,
        side: str,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._device = device
        self._zone_id = zone_id
        self._side = side

        self._attr_unique_id = f"{device_id}_{zone_id}_climate"
        self._attr_translation_key = f"bed_climate_{side}"

        temp_range = device.get("temperature_range", {})
        self._attr_min_temp = float(temp_range.get("min", 10))
        self._attr_max_temp = float(temp_range.get("max", 45))
        self._attr_target_temperature_step = 0.5

    def _zone_live(self) -> dict | None:
        return self.coordinator.get_zone_live(self._device_id, self._zone_id)

    @property
    def current_temperature(self) -> float | None:
        """Return the current measured bed temperature from the live WS snapshot.

        Reads ``status.zones[].temp`` (what the hardware actually measures)
        rather than the insights session data, which can be hours old.
        """
        zone = self.coordinator.get_zone_measured(self._device_id, self._zone_id)
        if zone is None:
            return None
        temp = zone.get("temp")
        return float(temp) if temp is not None else None

    @property
    def target_temperature(self) -> float | None:
        """Return the zone's active target temperature from the live snapshot."""
        zone = self._zone_live()
        if zone is None:
            return None
        temp = zone.get("temp")
        return float(temp) if temp is not None else None

    @property
    def hvac_mode(self) -> HVACMode:
        """Return HEAT_COOL if this zone is powered on, OFF otherwise."""
        zone = self._zone_live()
        if zone is None:
            return HVACMode.OFF
        return HVACMode.HEAT_COOL if zone.get("on") else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the zone's current action based on its thermal state."""
        zone = self._zone_live()
        if not zone or not zone.get("on"):
            return HVACAction.OFF
        measured = self.coordinator.get_zone_measured(self._device_id, self._zone_id)
        state = (measured or {}).get("thermal_state")
        if state is None:
            return None
        if "heat" in state.lower():
            return HVACAction.HEATING
        if "cool" in state.lower():
            return HVACAction.COOLING
        return HVACAction.IDLE

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature for this zone.

        Also turns the zone on if it is currently off, which is the standard
        HA expectation when a user sets a temperature on an off climate entity.
        """
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        serial = self._get_device().get("serial_number")
        if not serial:
            _LOGGER.error("No serial_number for device %s", self._device_id)
            return
        zone_live = self._zone_live()
        turn_on = zone_live is None or not zone_live.get("on")
        await self.coordinator.api_client.update_live_device_zone(
            serial, self._zone_id, on=True if turn_on else None, temp=temp
        )
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode by toggling this zone's power state."""
        serial = self._get_device().get("serial_number")
        if not serial:
            _LOGGER.error("No serial_number for device %s", self._device_id)
            return
        on = hvac_mode == HVACMode.HEAT_COOL
        await self.coordinator.api_client.update_live_device_zone(
            serial, self._zone_id, on=on
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        """Turn on this zone."""
        await self.async_set_hvac_mode(HVACMode.HEAT_COOL)

    async def async_turn_off(self) -> None:
        """Turn off this zone."""
        await self.async_set_hvac_mode(HVACMode.OFF)
