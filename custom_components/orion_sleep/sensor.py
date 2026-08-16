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
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ZONE_LEFT, DEFAULT_ZONE_LEFT
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

    zone_left = entry.options.get(CONF_ZONE_LEFT, DEFAULT_ZONE_LEFT)
    zone_right = "zone_b" if zone_left == "zone_a" else "zone_a"
    zone_sides = [(zone_left, "left"), (zone_right, "right")]

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
        for zone_id, side in zone_sides:
            entities.append(
                OrionMeasuredZoneTempSensor(coordinator, device_id, zone_id, side)
            )
            entities.append(
                OrionZoneThermalStateSensor(coordinator, device_id, zone_id, side)
            )
            entities.append(
                OrionZoneSleepScoreSensor(coordinator, device_id, zone_id, side)
            )
            entities.append(
                OrionZoneHrvSensor(coordinator, device_id, zone_id, side)
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

        session = self.coordinator.get_latest_session()
        return self.entity_description.value_fn(session)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""
        if not self.coordinator.data:
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


class OrionMeasuredZoneTempSensor(OrionBaseEntity, SensorEntity):
    """Real-time measured bed temperature for one zone from the WS stream.

    Reads ``status.zones[].temp`` from the live_device payload — the actual
    temperature the hardware measures, updated every ~2s via WebSocket.
    Distinct from the zone setpoint (``zones[].temp``) returned by
    ``get_zone_live()``.
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:thermometer"

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
        zone_id: str,
        side: str,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._zone_id = zone_id
        self._attr_unique_id = f"{device_id}_{zone_id}_measured_temp"
        self._attr_translation_key = f"measured_temp_{side}"

    @property
    def available(self) -> bool:
        return self.coordinator.get_zone_measured(self._device_id, self._zone_id) is not None

    @property
    def native_value(self) -> float | None:
        zone = self.coordinator.get_zone_measured(self._device_id, self._zone_id)
        if zone is None:
            return None
        temp = zone.get("temp")
        return float(temp) if temp is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        zone = self.coordinator.get_zone_measured(self._device_id, self._zone_id)
        if not zone:
            return None
        thermal_state = zone.get("thermal_state")
        return {"thermal_state": thermal_state} if thermal_state is not None else None


class OrionZoneThermalStateSensor(OrionBaseEntity, SensorEntity):
    """Thermal operating state for one zone (standby / heating / cooling).

    Reads ``status.zones[].thermal_state`` from the live WS payload.
    Only ``standby`` has been observed in captures; ``heating`` and
    ``cooling`` are expected but unconfirmed.
    """

    _attr_icon = "mdi:thermometer-auto"

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
        zone_id: str,
        side: str,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._zone_id = zone_id
        self._attr_unique_id = f"{device_id}_{zone_id}_thermal_state"
        self._attr_translation_key = f"thermal_state_{side}"

    @property
    def available(self) -> bool:
        return self.coordinator.get_zone_measured(self._device_id, self._zone_id) is not None

    @property
    def native_value(self) -> str | None:
        zone = self.coordinator.get_zone_measured(self._device_id, self._zone_id)
        if zone is None:
            return None
        return zone.get("thermal_state")


class OrionZoneSleepScoreSensor(OrionBaseEntity, SensorEntity):
    """Sleep score for one side of the bed.

    Reads the ``score`` from that zone's most recent sleep session so a
    two-person bed gets a distinct left- and right-side score, instead of
    the single account-level score the app shows in its overview. The
    ``quality_rating`` attribute mirrors the app's Excellent/Good/Fair/Poor
    label.
    """

    _attr_native_unit_of_measurement = "points"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:medal-outline"

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
        zone_id: str,
        side: str,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._zone_id = zone_id
        self._attr_unique_id = f"{device_id}_{zone_id}_sleep_score"
        self._attr_translation_key = f"sleep_score_{side}"

    def _score(self) -> float | None:
        session = self.coordinator.get_latest_session_for_zone(self._zone_id)
        if not session:
            return None
        return session.get("score")

    @property
    def native_value(self) -> float | None:
        return self._score()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        quality = _score_quality(self._score())
        return {"quality_rating": quality} if quality else None


class OrionZoneHrvSensor(OrionBaseEntity, SensorEntity):
    """Heart-rate variability for one side of the bed.

    Reads the current HRV value from that zone's most recent sleep session
    so each side of a two-person bed gets its own entity. We surface the
    single value the API reports (``hrv.average``) rather than min/max — at
    the polling cadence this tracks the latest reading closely enough for
    graphing and automations.
    """

    _attr_native_unit_of_measurement = "ms"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:heart-flash"

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
        zone_id: str,
        side: str,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._zone_id = zone_id
        self._attr_unique_id = f"{device_id}_{zone_id}_hrv"
        self._attr_translation_key = f"hrv_{side}"

    @property
    def native_value(self) -> float | None:
        session = self.coordinator.get_latest_session_for_zone(self._zone_id)
        if not session:
            return None
        return session.get("hrv", {}).get("average")
