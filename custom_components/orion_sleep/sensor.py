"""Sensor platform for Orion Sleep."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import OrionDataUpdateCoordinator
from .entity import OrionBaseEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class OrionSensorEntityDescription(SensorEntityDescription):
    """Describe an Orion Sleep sensor."""

    value_fn: Callable[[dict], Any]


def _get_latest_insight(data: dict) -> dict | None:
    """Get the most recent daily sleep insight."""
    insights = data.get("insights", {})
    daily = insights.get("dailySleepInsights", [])
    if not daily:
        return None
    return daily[-1]


SENSOR_DESCRIPTIONS: tuple[OrionSensorEntityDescription, ...] = (
    OrionSensorEntityDescription(
        key="sleep_score",
        translation_key="sleep_score",
        native_unit_of_measurement="points",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            insight.get("sleepScore")
            if (insight := _get_latest_insight(data))
            else None
        ),
    ),
    OrionSensorEntityDescription(
        key="hrv",
        translation_key="hrv",
        native_unit_of_measurement="ms",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            (insight.get("hrv") or {}).get("value")
            if (insight := _get_latest_insight(data))
            else None
        ),
    ),
    OrionSensorEntityDescription(
        key="breath_rate",
        translation_key="breath_rate",
        native_unit_of_measurement="breaths/min",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            (insight.get("breathRate") or {}).get("value")
            if (insight := _get_latest_insight(data))
            else None
        ),
    ),
    OrionSensorEntityDescription(
        key="body_movement_rate",
        translation_key="body_movement_rate",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            (insight.get("bodyMovement") or {}).get("rate")
            if (insight := _get_latest_insight(data))
            else None
        ),
    ),
    OrionSensorEntityDescription(
        key="restless_time",
        translation_key="restless_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            (insight.get("bodyMovement") or {}).get("restlessTime")
            if (insight := _get_latest_insight(data))
            else None
        ),
    ),
    OrionSensorEntityDescription(
        key="times_left_bed",
        translation_key="times_left_bed",
        native_unit_of_measurement="times",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            (insight.get("bodyMovement") or {}).get("leftBed")
            if (insight := _get_latest_insight(data))
            else None
        ),
    ),
    OrionSensorEntityDescription(
        key="total_sleep_time",
        translation_key="total_sleep_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            insight.get("totalSleepTime")
            if (insight := _get_latest_insight(data))
            else None
        ),
    ),
    OrionSensorEntityDescription(
        key="awake_time",
        translation_key="awake_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            (insight.get("sleepStages") or {}).get("awake")
            if (insight := _get_latest_insight(data))
            else None
        ),
    ),
    OrionSensorEntityDescription(
        key="light_sleep_time",
        translation_key="light_sleep_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            (insight.get("sleepStages") or {}).get("light")
            if (insight := _get_latest_insight(data))
            else None
        ),
    ),
    OrionSensorEntityDescription(
        key="deep_sleep_time",
        translation_key="deep_sleep_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            (insight.get("sleepStages") or {}).get("deep")
            if (insight := _get_latest_insight(data))
            else None
        ),
    ),
    OrionSensorEntityDescription(
        key="rem_sleep_time",
        translation_key="rem_sleep_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            (insight.get("sleepStages") or {}).get("rem")
            if (insight := _get_latest_insight(data))
            else None
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Orion Sleep sensor entities."""
    coordinator: OrionDataUpdateCoordinator = entry.runtime_data
    entities: list[OrionSensorEntity] = []

    for device in coordinator.devices:
        device_id = device.get("deviceId") or device.get("id")
        if not device_id:
            continue
        for description in SENSOR_DESCRIPTIONS:
            entities.append(OrionSensorEntity(coordinator, device_id, description))

    async_add_entities(entities)


class OrionSensorEntity(OrionBaseEntity, SensorEntity):
    """Sensor entity for Orion Sleep insights."""

    entity_description: OrionSensorEntityDescription

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
        description: OrionSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
