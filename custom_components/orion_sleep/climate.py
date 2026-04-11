"""Climate platform for Orion Sleep."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.climate.const import PRESET_AWAY, PRESET_NONE
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
    """Set up Orion Sleep climate entities."""
    coordinator: OrionDataUpdateCoordinator = entry.runtime_data
    entities: list[OrionClimateEntity] = []

    for cfg in (coordinator.data or {}).get("sleep_configs", []):
        device_id = cfg.get("deviceId")
        if not device_id:
            continue
        if cfg.get("splitZones"):
            entities.append(OrionClimateEntity(coordinator, device_id, side="left"))
            entities.append(OrionClimateEntity(coordinator, device_id, side="right"))
        else:
            entities.append(OrionClimateEntity(coordinator, device_id))

    async_add_entities(entities)


class OrionClimateEntity(OrionBaseEntity, ClimateEntity):
    """Climate entity for an Orion Sleep bed zone."""

    _attr_hvac_modes = [HVACMode.HEAT_COOL, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.PRESET_MODE
    )
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_preset_modes = [PRESET_NONE, PRESET_AWAY]
    _enable_turn_on_off_backwards_compat = False

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
        side: str | None = None,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._side = side
        if side:
            self._attr_unique_id = f"{device_id}_{side}"
            self._attr_translation_key = f"bed_climate_{side}"
        else:
            self._attr_unique_id = device_id
            self._attr_translation_key = "bed_climate"

    def _get_user_zone(self) -> dict | None:
        """Get the user zone matching this entity's side."""
        config = self._get_sleep_config()
        if not config:
            return None
        users = config.get("users", [])
        if not self._side:
            # No split zones — return first user zone if available
            return users[0] if users else None
        for user in users:
            if user.get("side") == self._side:
                return user
        return None

    @property
    def current_temperature(self) -> float | None:
        """Return the current bed temperature."""
        config = self._get_sleep_config()
        if not config:
            return None
        temp = config.get("temperature", {})
        return temp.get("current")

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        config = self._get_sleep_config()
        if not config:
            return None

        if self._side:
            # Split zones: get target from user zone
            zone = self._get_user_zone()
            if zone:
                return zone.get("targetTemperature")
        # Non-split or fallback: use top-level temperature target
        temp = config.get("temperature", {})
        return temp.get("target")

    @property
    def target_temperature_step(self) -> float | None:
        """Return the temperature step."""
        config = self._get_sleep_config()
        if not config:
            return 1.0
        increment = config.get("temperature", {}).get("controlIncrement", {})
        return increment.get("fahrenheit", 1.0)

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        config = self._get_sleep_config()
        if config:
            temp_range = config.get("temperature", {}).get("range", {})
            min_val = temp_range.get("minFahrenheit")
            if min_val is not None:
                return min_val
        return 55.0

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        config = self._get_sleep_config()
        if config:
            temp_range = config.get("temperature", {}).get("range", {})
            max_val = temp_range.get("maxFahrenheit")
            if max_val is not None:
                return max_val
        return 115.0

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
        zone = self._get_user_zone()
        if zone and zone.get("isAway"):
            return HVACMode.OFF

        session = (self.coordinator.data or {}).get("session_state", {})
        if session.get("active"):
            return HVACMode.HEAT_COOL

        return HVACMode.HEAT_COOL

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        zone = self._get_user_zone()
        if zone and zone.get("isAway"):
            return PRESET_AWAY
        return PRESET_NONE

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        await self.coordinator.api_client.set_temperature(
            device_id=self._device_id,
            temperature=temp,
            side=self._side,
        )
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.api_client.set_user_away(self._device_id, self._side)
        # HEAT_COOL: setting a temperature should un-away the device
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode."""
        if preset_mode == PRESET_AWAY:
            await self.coordinator.api_client.set_user_away(self._device_id, self._side)
            await self.coordinator.async_request_refresh()
        elif preset_mode == PRESET_NONE:
            # Un-away by setting current target temperature
            target = self.target_temperature
            if target is not None:
                await self.coordinator.api_client.set_temperature(
                    device_id=self._device_id,
                    temperature=target,
                    side=self._side,
                )
                await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        """Turn on the climate entity."""
        # Setting temperature effectively turns it on
        target = self.target_temperature
        if target is not None:
            await self.coordinator.api_client.set_temperature(
                device_id=self._device_id,
                temperature=target,
                side=self._side,
            )
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        """Turn off the climate entity."""
        await self.coordinator.api_client.set_user_away(self._device_id, self._side)
        await self.coordinator.async_request_refresh()
