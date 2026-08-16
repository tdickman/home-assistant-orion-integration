"""Climate platform for Orion Sleep.

One climate entity per *zone*, not per device. A dual-zone bed genuinely
runs two independent setpoints — the live snapshot shows them diverging —
so collapsing them into a single entity throws away half the control
surface.

All reads and writes go through the live runtime endpoints:

    GET /v1/devices/{serial}/live                  setpoint + measured
    PUT /v1/devices/{serial}/live/zones/{zoneId}   on / temp

These are the endpoints the app itself drives and they have been observed
on the wire. The previous device-level entity wrote through
`PUT /v1/sleep-configurations/temperature`, which is documented in
`openapi.yaml` as **never verified against the live API**, and its
`turn_off` was a no-op.
"""

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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import OrionDataUpdateCoordinator
from .entity import OrionBaseEntity

_LOGGER = logging.getLogger(__name__)

# Only "standby" has ever been captured on the wire. The heating/cooling
# spellings are guesses from the field name, so they are listed here but
# anything unrecognised deliberately falls through to None ("unknown")
# rather than being rendered as a confident direction.
_THERMAL_STATE_TO_ACTION: dict[str, HVACAction] = {
    "standby": HVACAction.IDLE,
    "idle": HVACAction.IDLE,
    "off": HVACAction.OFF,
    "heating": HVACAction.HEATING,
    "heat": HVACAction.HEATING,
    "cooling": HVACAction.COOLING,
    "cool": HVACAction.COOLING,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one climate entity per zone, per device."""
    coordinator: OrionDataUpdateCoordinator = entry.runtime_data
    entities: list[OrionZoneClimateEntity] = []

    for device in coordinator.devices:
        device_id = device.get("id")
        if not device_id:
            continue

        zone_ids = coordinator.device_zone_ids(device_id)
        if not zone_ids:
            # No live snapshot yet and no membership list. Skipping is
            # correct: inventing a synthetic zone would create an entity
            # whose unique_id can never match the real one later.
            _LOGGER.warning(
                "Orion device %s exposed no zones; no climate entity created. "
                "This resolves itself once the first live snapshot arrives",
                device_id,
            )
            continue

        for zone_id in zone_ids:
            entities.append(OrionZoneClimateEntity(coordinator, device_id, zone_id))

    async_add_entities(entities)


class OrionZoneClimateEntity(OrionBaseEntity, ClimateEntity):
    """A single temperature zone (one side of the bed)."""

    _attr_hvac_modes = [HVACMode.HEAT_COOL, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    # The API is Celsius-native; HA converts for display. Reporting the
    # native unit is what makes °F households render correctly.
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _enable_turn_on_off_backwards_compat = False

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
        zone_id: str,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._zone_id = zone_id
        self._attr_unique_id = f"{device_id}_climate_{zone_id}"
        self._attr_name = f"{self._zone_label()} Climate"

        temp_range = self._get_device().get("temperature_range", {})
        self._attr_min_temp = float(temp_range.get("min", 10))
        self._attr_max_temp = float(temp_range.get("max", 45))

    def _zone_label(self) -> str:
        """Human label for this zone.

        Prefers the assigned user's first name, because that is how the
        bed is actually discussed ("my side"). Falls back to the zone id.

        Deliberately NOT left/right: the device does carry an
        `orientation` field, but the zone -> side mapping has never been
        verified against a split-occupancy capture, and a confidently
        mislabelled side is worse than a neutral one.
        """
        for zone in self._get_device().get("zones", []) or []:
            if zone.get("id") == self._zone_id:
                user = zone.get("user") or {}
                first = (user.get("first_name") or "").strip()
                if first:
                    return first
                break
        return self._zone_id.replace("_", " ").title()

    def _serial(self) -> str:
        """Serial number — the live endpoints reject the UUID with a 403."""
        serial = self._get_device().get("serial_number")
        if not serial:
            raise HomeAssistantError(
                f"No serial_number for Orion device {self._device_id}; "
                "cannot address the live zone endpoint"
            )
        return str(serial)

    @property
    def available(self) -> bool:
        """Available only when a live snapshot actually contains this zone."""
        return (
            super().available
            and self.coordinator.zone_is_on(self._device_id, self._zone_id) is not None
        )

    @property
    def current_temperature(self) -> float | None:
        """Measured zone temperature (`status.zones[].temp`)."""
        return self.coordinator.zone_measured_temp(self._device_id, self._zone_id)

    @property
    def target_temperature(self) -> float | None:
        """Zone setpoint (`zones[].temp`)."""
        return self.coordinator.zone_setpoint(self._device_id, self._zone_id)

    @property
    def hvac_mode(self) -> HVACMode:
        """HEAT_COOL when the zone is powered, OFF otherwise."""
        return (
            HVACMode.HEAT_COOL
            if self.coordinator.zone_is_on(self._device_id, self._zone_id)
            else HVACMode.OFF
        )

    @property
    def hvac_action(self) -> HVACAction | None:
        """What the zone is doing right now, when the device says so."""
        if not self.coordinator.zone_is_on(self._device_id, self._zone_id):
            return HVACAction.OFF
        state = self.coordinator.zone_thermal_state(self._device_id, self._zone_id)
        if state is None:
            return None
        return _THERMAL_STATE_TO_ACTION.get(state.lower())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the zone id and the raw thermal state for debugging."""
        return {
            "zone_id": self._zone_id,
            "thermal_state": self.coordinator.zone_thermal_state(
                self._device_id, self._zone_id
            ),
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set this zone's target temperature."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        await self.coordinator.api_client.update_live_device_zone(
            device_serial=self._serial(),
            zone_id=self._zone_id,
            temp=float(temp),
        )
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Turn this zone on or off."""
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
        else:
            await self.async_turn_on()

    async def async_turn_on(self) -> None:
        """Power this zone on, leaving its setpoint untouched."""
        await self.coordinator.api_client.update_live_device_zone(
            device_serial=self._serial(),
            zone_id=self._zone_id,
            on=True,
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        """Power this zone off.

        NOTE: this is a *runtime* off. The sleep schedule is a separate
        object and will power the zone back on at its next scheduled
        action — see `_handle_schedule_on_turn_off`.
        """
        await self.coordinator.api_client.update_live_device_zone(
            device_serial=self._serial(),
            zone_id=self._zone_id,
            on=False,
        )
        await self._handle_schedule_on_turn_off()
        await self.coordinator.async_request_refresh()

    async def _handle_schedule_on_turn_off(self) -> None:
        """Reconcile a manual zone-off against tonight's sleep schedule.

        POLICY: passthrough (chosen 2026-07-26). Turning a zone off powers
        it down immediately and does NOT touch the schedule, so the next
        scheduled action powers it back on. This matches the vendor app,
        and it keeps a climate card from silently mutating a schedule the
        other sleeper also depends on.

        Consequence to be aware of: "I turned it off and it came back on
        at bedtime" is expected behaviour, not a bug.

        To change the policy later, everything needed is already here:
            self.coordinator.get_today_schedule()  -> today's schedule dict,
                carrying `bedtime_is_active` / `wakeup_is_active`
            self.coordinator.api_client            -> PUT /v1/sleep-schedules
                body: {"schedules": [{"day": N, <field>: <value>}]}
                (partial updates work — only the named field changes)
        Note the schedule is per-USER, not per-zone, so a schedule edit
        made from one zone's card affects that user's whole schedule.
        """
        return
