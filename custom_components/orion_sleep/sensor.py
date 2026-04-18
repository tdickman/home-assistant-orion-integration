"""Sensor platform for Orion Sleep."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import OrionDataUpdateCoordinator
from .entity import OrionBaseEntity


# Topper sensors exposed on every WS payload. Mapping to zone_a/zone_b
# isn't verified yet, so entities are named per sensor.
_TOPPER_SENSORS: tuple[str, ...] = ("sensor1", "sensor2")

_LOGGER = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────


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


def _minutes_to_hm(minutes: float | int | None) -> str | None:
    """Convert minutes to 'Xh Ym' string like the app shows."""
    if minutes is None:
        return None
    total = int(round(minutes))
    h, m = divmod(total, 60)
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"


def _seconds_to_ms(seconds: float | int | None) -> str | None:
    """Convert seconds to 'Xm Ys' string like the app shows."""
    if seconds is None:
        return None
    total = int(round(seconds))
    m, s = divmod(total, 60)
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def _score_quality(score: float | int | None) -> str | None:
    """Return a quality label for a sleep score, matching the app's rating."""
    if score is None:
        return None
    if score >= 90:
        return "Excellent"
    if score >= 80:
        return "Good"
    if score >= 60:
        return "Fair"
    return "Poor"


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


# ── Sensor descriptions ───────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class OrionSensorEntityDescription(SensorEntityDescription):
    """Describe an Orion Sleep sensor."""

    value_fn: Callable[[dict | None], Any]
    extra_attrs_fn: Callable[[dict | None], dict[str, Any]] | None = None
    icon: str | None = None


# Duration sensors: we intentionally do NOT set device_class=DURATION.
# HA's DURATION device class overrides entity names on device pages with a
# generic "Duration" label, making all sleep duration sensors indistinguishable.
# Instead we format the values ourselves as human-friendly strings (7h 53m).

INSIGHT_SENSOR_DESCRIPTIONS: tuple[OrionSensorEntityDescription, ...] = (
    OrionSensorEntityDescription(
        key="sleep_score",
        translation_key="sleep_score",
        native_unit_of_measurement="points",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:medal-outline",
        value_fn=lambda session: None,  # handled specially in the entity
        extra_attrs_fn=lambda session: {},  # handled specially in the entity
    ),
    OrionSensorEntityDescription(
        key="total_sleep_time",
        translation_key="total_sleep_time",
        icon="mdi:sleep",
        value_fn=lambda session: _minutes_to_hm(
            _get_sleep_summary(session).get("time_asleep")
        ),
    ),
    OrionSensorEntityDescription(
        key="deep_sleep_time",
        translation_key="deep_sleep_time",
        icon="mdi:power-sleep",
        value_fn=lambda session: _minutes_to_hm(
            _get_sleep_summary(session).get("deep_sleep")
        ),
    ),
    OrionSensorEntityDescription(
        key="rem_sleep_time",
        translation_key="rem_sleep_time",
        icon="mdi:eye-refresh-outline",
        value_fn=lambda session: _minutes_to_hm(
            _get_sleep_summary(session).get("rem_sleep")
        ),
    ),
    OrionSensorEntityDescription(
        key="light_sleep_time",
        translation_key="light_sleep_time",
        icon="mdi:weather-night",
        value_fn=lambda session: _minutes_to_hm(
            _get_sleep_summary(session).get("light_sleep")
        ),
    ),
    OrionSensorEntityDescription(
        key="awake_time",
        translation_key="awake_time",
        icon="mdi:eye-outline",
        value_fn=lambda session: _minutes_to_hm(
            _get_sleep_summary(session).get("awake_time")
        ),
    ),
    OrionSensorEntityDescription(
        key="heart_rate_avg",
        translation_key="heart_rate_avg",
        native_unit_of_measurement="bpm",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:heart-pulse",
        value_fn=lambda session: _get_heart_rate(session).get("average"),
        extra_attrs_fn=lambda session: {
            "min": _get_heart_rate(session).get("min"),
            "max": _get_heart_rate(session).get("max"),
            "range": (
                f"{_get_heart_rate(session).get('min')} - {_get_heart_rate(session).get('max')}"
                if _get_heart_rate(session).get("min") is not None
                and _get_heart_rate(session).get("max") is not None
                else None
            ),
        },
    ),
    OrionSensorEntityDescription(
        key="breath_rate",
        translation_key="breath_rate",
        native_unit_of_measurement="breaths/min",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lungs",
        value_fn=lambda session: _get_breath_rate(session).get("average"),
        extra_attrs_fn=lambda session: {
            "min": _get_breath_rate(session).get("min"),
            "max": _get_breath_rate(session).get("max"),
            "range": (
                f"{_get_breath_rate(session).get('min')} - {_get_breath_rate(session).get('max')}"
                if _get_breath_rate(session).get("min") is not None
                and _get_breath_rate(session).get("max") is not None
                else None
            ),
        },
    ),
    OrionSensorEntityDescription(
        key="hrv",
        translation_key="hrv",
        native_unit_of_measurement="ms",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:heart-flash",
        value_fn=lambda session: _get_hrv(session).get("average"),
        extra_attrs_fn=lambda session: {
            "min": _get_hrv(session).get("min"),
            "max": _get_hrv(session).get("max"),
        },
    ),
    OrionSensorEntityDescription(
        key="body_movement_rate",
        translation_key="body_movement_rate",
        native_unit_of_measurement="/hr",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:run",
        value_fn=lambda session: _get_movement(session).get("movement_rate"),
    ),
    OrionSensorEntityDescription(
        key="restless_time",
        translation_key="restless_time",
        icon="mdi:motion-sensor",
        # Format as human-friendly string like the app (3m 36s)
        value_fn=lambda session: _seconds_to_ms(
            _get_movement(session).get("total_seconds")
        ),
    ),
)

# Schedule sensors — derived from today_sleep_schedule, not sessions

SCHEDULE_SENSOR_DESCRIPTIONS: tuple[OrionSensorEntityDescription, ...] = (
    OrionSensorEntityDescription(
        key="bedtime",
        translation_key="bedtime",
        icon="mdi:bed-clock",
        value_fn=lambda schedule: schedule.get("bedtime") if schedule else None,
    ),
    OrionSensorEntityDescription(
        key="wakeup_time",
        translation_key="wakeup_time",
        icon="mdi:alarm",
        value_fn=lambda schedule: schedule.get("wakeup") if schedule else None,
    ),
    OrionSensorEntityDescription(
        key="schedule_duration",
        translation_key="schedule_duration",
        icon="mdi:timer-sand",
        value_fn=lambda schedule: _calc_schedule_duration(schedule),
    ),
    OrionSensorEntityDescription(
        key="bedtime_temp",
        translation_key="bedtime_temp",
        native_unit_of_measurement="°C",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer-lines",
        value_fn=lambda schedule: schedule.get("bedtime_temp") if schedule else None,
        extra_attrs_fn=lambda schedule: _schedule_temp_attrs(schedule),
    ),
    OrionSensorEntityDescription(
        key="wakeup_temp",
        translation_key="wakeup_temp",
        native_unit_of_measurement="°C",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer-alert",
        value_fn=lambda schedule: schedule.get("wakeup_temp") if schedule else None,
    ),
)


def _calc_schedule_duration(schedule: dict | None) -> str | None:
    """Calculate the duration between bedtime and wakeup as 'Xh Ym'."""
    if not schedule:
        return None
    bedtime = schedule.get("bedtime")
    wakeup = schedule.get("wakeup")
    if not bedtime or not wakeup:
        return None
    try:
        bh, bm = map(int, bedtime.split(":"))
        wh, wm = map(int, wakeup.split(":"))
        bed_mins = bh * 60 + bm
        wake_mins = wh * 60 + wm
        if wake_mins <= bed_mins:
            # Wakeup is next day
            wake_mins += 24 * 60
        total = wake_mins - bed_mins
        h, m = divmod(total, 60)
        return f"{h}h {m}m"
    except (ValueError, AttributeError):
        return None


def _schedule_temp_attrs(schedule: dict | None) -> dict[str, Any]:
    """Extra attributes for the bedtime temp sensor showing the full temp curve."""
    if not schedule:
        return {}
    attrs: dict[str, Any] = {}
    for key in ("phase_1_temp", "phase_2_temp", "wakeup_temp"):
        val = schedule.get(key)
        if val is not None:
            attrs[key] = val
    if schedule.get("is_smart_temperature_active") is not None:
        attrs["smart_temperature"] = schedule["is_smart_temperature_active"]
    return attrs


# ── Setup ─────────────────────────────────────────────────────────────────


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Orion Sleep sensor entities."""
    coordinator: OrionDataUpdateCoordinator = entry.runtime_data
    entities: list[SensorEntity] = []

    for device in coordinator.devices:
        device_id = device.get("id")
        if not device_id:
            continue
        for description in INSIGHT_SENSOR_DESCRIPTIONS:
            entities.append(OrionSensorEntity(coordinator, device_id, description))
        for description in SCHEDULE_SENSOR_DESCRIPTIONS:
            entities.append(
                OrionScheduleSensorEntity(coordinator, device_id, description)
            )
        entities.append(OrionCurrentTempOffsetSensor(coordinator, device_id))
        entities.append(OrionCurrentTempOffsetSensor(coordinator, device_id))
        entities.append(OrionWebSocketStateSensor(coordinator, device_id))
        for sensor_name in _TOPPER_SENSORS:
            entities.append(
                OrionLiveHeartRateSensor(coordinator, device_id, sensor_name)
            )
            entities.append(
                OrionLiveBreathRateSensor(coordinator, device_id, sensor_name)
            )
            entities.append(
                OrionSensorStatusTextSensor(coordinator, device_id, sensor_name)
            )

    async_add_entities(entities)


# ── Entities ──────────────────────────────────────────────────────────────


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

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""
        if not self.coordinator.data:
            return None

        # Sleep score gets the quality rating
        if self.entity_description.key == "sleep_score":
            score = _get_score(self.coordinator.data)
            quality = _score_quality(score)
            if quality:
                return {"quality_rating": quality}
            return None

        if self.entity_description.extra_attrs_fn is None:
            return None
        session = self.coordinator.get_latest_session()
        attrs = self.entity_description.extra_attrs_fn(session)
        # Filter out None values
        return {k: v for k, v in attrs.items() if v is not None} or None


class OrionScheduleSensorEntity(OrionBaseEntity, SensorEntity):
    """Sensor entity for Orion Sleep schedule data."""

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
        """Return the sensor value from today's schedule."""
        schedule = self.coordinator.get_today_schedule()
        return self.entity_description.value_fn(schedule)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""
        if self.entity_description.extra_attrs_fn is None:
            return None
        schedule = self.coordinator.get_today_schedule()
        attrs = self.entity_description.extra_attrs_fn(schedule)
        return {k: v for k, v in attrs.items() if v is not None} or None


class OrionCurrentTempOffsetSensor(OrionBaseEntity, SensorEntity):
    """Sensor showing the current measured bed temperature as an app-style offset.

    The Orion app displays bed temperature as a relative offset,
    e.g. -3, 0, +5. This sensor shows the actual measured temperature
    offset from the latest sleep session — the value labeled "Now" in
    the app's temperature curve.

    Uses the device's temperature_scale.relative lookup table for
    accurate non-linear conversion.
    """

    _attr_translation_key = "current_temp_offset"
    _attr_icon = "mdi:thermometer"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_current_temp_offset"

    @property
    def native_value(self) -> float | None:
        """Return the current measured temperature offset."""
        session = self.coordinator.get_latest_session()
        if not session:
            return None
        temp_data = session.get("temperature", {})
        values = temp_data.get("values", [])
        if values:
            return self._celsius_to_offset(values[-1])
        return None


class OrionWebSocketStateSensor(OrionBaseEntity, SensorEntity):
    """Diagnostic sensor exposing the live-device WebSocket state.

    Mirrors the Android app's ``connectionState`` enum. Useful for
    automations that should pause when the device is unreachable.
    """

    _attr_translation_key = "websocket_state"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:lan-connect"

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_websocket_state"

    def _serial(self) -> str | None:
        device = self._get_device()
        return device.get("serial_number")

    @property
    def native_value(self) -> str | None:
        serial = self._serial()
        if not serial:
            return None
        return self.coordinator.ws_state(serial)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        serial = self._serial()
        if not serial:
            return None
        last_at = self.coordinator.ws_last_message_at(serial)
        if not last_at:
            return {"seconds_since_last_message": None}
        import time

        return {"seconds_since_last_message": round(time.monotonic() - last_at, 1)}

    @property
    def available(self) -> bool:
        # Always show the state — that's the whole point of this sensor.
        return True


class _OrionLiveSensorBase(OrionBaseEntity, SensorEntity):
    """Shared plumbing for per-topper-sensor live entities."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
        sensor_name: str,
        unique_suffix: str,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._sensor_name = sensor_name
        self._attr_unique_id = f"{device_id}_{sensor_name}_{unique_suffix}"

    @property
    def available(self) -> bool:
        # Available whenever we've seen any live frame for this device.
        return (
            self.coordinator.sensor_status_text(self._device_id, self._sensor_name)
            is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        block = self.coordinator._sensor_block(  # noqa: SLF001
            self._device_id, self._sensor_name
        )
        if not block:
            return None
        return {
            "status_text": block.get("status_text"),
            "is_working": block.get("is_working"),
            "firmware_version": block.get("firmware_version"),
            "hardware_version": block.get("hardware_version"),
        }


class OrionLiveHeartRateSensor(_OrionLiveSensorBase):
    """Realtime heart-rate reading from one topper sensor.

    Sourced from the WS ``status.sensors.<sensor>.heart_rate`` field.
    The raw value is 0 when the bed is empty and 255 when the sensor
    has no reading yet — both are mapped to ``None`` so automations
    don't react to sentinels. This is distinct from the post-session
    ``heart_rate_avg`` insight sensor, which only updates after Orion's
    cloud aggregates a completed session.
    """

    # HR isn't one of HA's built-in sensor device classes, so leave
    # device_class unset and surface the value + unit only.
    _attr_native_unit_of_measurement = "bpm"
    _attr_icon = "mdi:heart-pulse"

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
        sensor_name: str,
    ) -> None:
        super().__init__(coordinator, device_id, sensor_name, "live_heart_rate")
        self._attr_translation_key = f"{sensor_name}_live_heart_rate"

    @property
    def native_value(self) -> int | None:
        return self.coordinator.sensor_heart_rate(self._device_id, self._sensor_name)


class OrionLiveBreathRateSensor(_OrionLiveSensorBase):
    """Realtime breath-rate reading from one topper sensor."""

    _attr_native_unit_of_measurement = "br/min"
    _attr_icon = "mdi:lungs"

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
        sensor_name: str,
    ) -> None:
        super().__init__(coordinator, device_id, sensor_name, "live_breath_rate")
        self._attr_translation_key = f"{sensor_name}_live_breath_rate"

    @property
    def native_value(self) -> int | None:
        return self.coordinator.sensor_breath_rate(self._device_id, self._sensor_name)


class OrionSensorStatusTextSensor(_OrionLiveSensorBase):
    """Diagnostic sensor exposing the raw ``status_text`` field.

    Observed values: ``left_bed``, ``normal``. Other values likely exist
    in the app's string tables (e.g. error states) but haven't been seen
    on the wire yet — surfacing the raw value makes it easy to catch new
    values without another integration release.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:sleep"
    _attr_state_class = None  # categorical, not numeric

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
        sensor_name: str,
    ) -> None:
        super().__init__(coordinator, device_id, sensor_name, "sensor_status")
        self._attr_translation_key = f"{sensor_name}_status_text"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.sensor_status_text(self._device_id, self._sensor_name)
