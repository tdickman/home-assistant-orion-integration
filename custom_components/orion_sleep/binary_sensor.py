"""Binary sensor platform for Orion Sleep."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import OrionDataUpdateCoordinator
from .entity import OrionBaseEntity

_LOGGER = logging.getLogger(__name__)


# Sensors exposed on every ``live_device.{snapshot,update}`` payload.
# Mapping to zone_a/zone_b isn't verified yet; we expose the raw names
# the server uses so the user can build their own side mapping.
_TOPPER_SENSORS: tuple[str, ...] = ("sensor1", "sensor2")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Orion Sleep binary sensor entities."""
    coordinator: OrionDataUpdateCoordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []

    for device in coordinator.devices:
        device_id = device.get("id")
        if not device_id:
            continue
        entities.append(OrionSessionActiveBinarySensor(coordinator, device_id))
        for sensor_name in _TOPPER_SENSORS:
            entities.append(
                OrionSensorOnBedBinarySensor(coordinator, device_id, sensor_name)
            )

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


class OrionSensorOnBedBinarySensor(OrionBaseEntity, BinarySensorEntity):
    """Per-topper-sensor occupancy detector.

    Drives off the WebSocket ``status.sensors.<sensor_name>.status_text``
    field: ``"left_bed"`` means empty, any other value (observed:
    ``"normal"``) means the sensor detects a person. Flips within ~2 s of
    a real bed event since updates arrive on every WS heartbeat.

    The two sensors (``sensor1`` / ``sensor2``) correspond to the two
    measurement pads in the topper. Their mapping to ``zone_a`` /
    ``zone_b`` has not been verified against a split-occupancy capture,
    so entities are named per sensor rather than per side.
    """

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_icon = "mdi:bed-outline"

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
        sensor_name: str,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._sensor_name = sensor_name
        self._attr_translation_key = f"{sensor_name}_on_bed"
        self._attr_unique_id = f"{device_id}_{sensor_name}_on_bed"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.sensor_is_on_bed(self._device_id, self._sensor_name)

    @property
    def available(self) -> bool:
        # Report available whenever we have a live payload at all,
        # even if the individual sensor hasn't reported yet.
        return (
            self.coordinator.sensor_status_text(self._device_id, self._sensor_name)
            is not None
        )
