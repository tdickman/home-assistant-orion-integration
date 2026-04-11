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

    value_fn: Callable[[dict | None], Any]


def _get_sleep_summary(session: dict | None) -> dict:
    """Get sleep_summary from a session."""
    if not session:
        return {}
    return session.get("sleep_summary", {})


def _get_heart_rate(session: dict | None) -> dict:
    """Get heart_rate from a session."""
    if not session:
        return {}
    return session.get("heart_rate", {})


def _get_breath_rate(session: dict | None) -> dict:
    """Get breath_rate from a session."""
    if not session:
        return {}
    return session.get("breath_rate", {})


def _get_hrv(session: dict | None) -> dict:
    """Get hrv from a session."""
    if not session:
        return {}
    return session.get("hrv", {})


def _get_movement(session: dict | None) -> dict:
    """Get movement from a session."""
    if not session:
        return {}
    return session.get("movement", {})


def _get_score(coordinator_data: dict) -> float | None:
    """Get the most recent sleep score from insights overview."""
    insights = coordinator_data.get("insights", {})
    overview = insights.get("overview", {})
    if not overview:
        # Fall back to data entries
        data = insights.get("data", {})
        for date_key in sorted(data.keys(), reverse=True):
            score = data[date_key].get("score")
            if score is not None:
                return score
        return None
    for date_key in sorted(overview.keys(), reverse=True):
        score = overview[date_key].get("score")
        if score is not None:
            return score
    return None


SENSOR_DESCRIPTIONS: tuple[OrionSensorEntityDescription, ...] = (
    OrionSensorEntityDescription(
        key="sleep_score",
        translation_key="sleep_score",
        native_unit_of_measurement="points",
        state_class=SensorStateClass.MEASUREMENT,
        # Sleep score comes from the insights overview, not a session
        value_fn=lambda session: None,  # handled specially in the entity
    ),
    OrionSensorEntityDescription(
        key="total_sleep_time",
        translation_key="total_sleep_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda session: _get_sleep_summary(session).get("time_asleep"),
    ),
    OrionSensorEntityDescription(
        key="deep_sleep_time",
        translation_key="deep_sleep_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda session: _get_sleep_summary(session).get("deep_sleep"),
    ),
    OrionSensorEntityDescription(
        key="rem_sleep_time",
        translation_key="rem_sleep_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda session: _get_sleep_summary(session).get("rem_sleep"),
    ),
    OrionSensorEntityDescription(
        key="light_sleep_time",
        translation_key="light_sleep_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda session: _get_sleep_summary(session).get("light_sleep"),
    ),
    OrionSensorEntityDescription(
        key="awake_time",
        translation_key="awake_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda session: _get_sleep_summary(session).get("awake_time"),
    ),
    OrionSensorEntityDescription(
        key="heart_rate_avg",
        translation_key="heart_rate_avg",
        native_unit_of_measurement="bpm",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda session: _get_heart_rate(session).get("average"),
    ),
    OrionSensorEntityDescription(
        key="breath_rate",
        translation_key="breath_rate",
        native_unit_of_measurement="breaths/min",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda session: _get_breath_rate(session).get("average"),
    ),
    OrionSensorEntityDescription(
        key="hrv",
        translation_key="hrv",
        native_unit_of_measurement="ms",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda session: _get_hrv(session).get("average"),
    ),
    OrionSensorEntityDescription(
        key="body_movement_rate",
        translation_key="body_movement_rate",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda session: _get_movement(session).get("movement_rate"),
    ),
    OrionSensorEntityDescription(
        key="restless_time",
        translation_key="restless_time",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda session: _get_movement(session).get("total_seconds"),
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
        device_id = device.get("id")
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

        # Sleep score is special — comes from overview, not session
        if self.entity_description.key == "sleep_score":
            return _get_score(self.coordinator.data)

        session = self.coordinator.get_latest_session()
        return self.entity_description.value_fn(session)
