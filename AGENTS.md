# Orion Sleep - Home Assistant HACS Integration

## Project Overview

HACS-compatible Home Assistant custom integration for the **Orion Sleep** smart mattress topper. Cloud-connected bed temperature control with per-zone support, sleep tracking (heart rate, breath rate, HRV, sleep stages), and sleep scheduling.

## Repository Structure

```
home-assistant-orion-integration/
├── hacs.json                          # HACS repo metadata
├── README.md                          # User-facing install/usage docs
├── openapi.yaml                       # OpenAPI 3.1 spec (reverse-engineered; WS section validated on-wire)
├── orion_info.py                      # Working CLI script (REST + WS capture tooling)
├── custom_components/
│   └── orion_sleep/
│       ├── __init__.py                # async_setup_entry / async_unload_entry
│       ├── manifest.json              # HA integration manifest (v1.0.0)
│       ├── const.py                   # DOMAIN, config keys, defaults, temp lookup table
│       ├── api.py                     # Async aiohttp API client
│       ├── coordinator.py             # DataUpdateCoordinator + data helpers
│       ├── config_flow.py             # Three-step auth flow + options flow
│       ├── entity.py                  # Base entity with DeviceInfo + temp conversion helpers
│       ├── climate.py                 # Bed temperature control
│       ├── sensor.py                  # Sleep insight + schedule + offset + WS state sensors (18 per device)
│       ├── websocket.py                # Live device WebSocket client (per-device aiohttp)
│       ├── binary_sensor.py           # Sleep session active
│       ├── switch.py                  # Power (user-away) + sleep schedule switches
│       ├── diagnostics.py             # Diagnostics with PII redaction
│       ├── strings.json               # UI translations
│       ├── translations/
│       │   └── en.json                # English translations (mirrors strings.json)
│       └── brand/                     # Integration icon (96px + 180px)
```

## Source-of-Truth Policy

Both `openapi.yaml` and `orion_info.py` are kept in sync as new endpoints or behaviors are discovered. The REST section of the spec is reverse-engineered from the Android bytecode with spot-checks against the live API; the WebSocket section (`/device/{serial_number}` path and `x-websocket` block) is validated by an on-wire capture (`orion_info.py --ws-scenario`). Neither file is inherently more authoritative — when they disagree, re-verify against the live server rather than trusting one blindly.

Known gaps and unverified endpoints are called out in the tables below. When adding or changing behavior:

1. Prefer running `orion_info.py --ws-scenario` (or the individual flags) against a live account to confirm on-wire shapes.
2. Update **both** `openapi.yaml` and the relevant comments/flags in `orion_info.py`.
3. Reflect any new limitations or caveats in this file.

### API Base URL

```
https://api1.orionbed.com
```

### Working Endpoints

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| POST | `/v1/auth/code` | No | Send verification code to email/phone |
| POST | `/v1/auth/verify` | No | Verify code, get tokens. Response nested: `response.session.{access_token, refresh_token, expires_at}` |
| POST | `/v1/auth/refresh` | No | Refresh tokens. Body: `{"refresh_token": "..."}`. Response may be nested or top-level. |
| GET | `/v1/auth/me` | Bearer | User profile. Wrapped in `{"response": {...}, "success": true}` |
| GET | `/v1/devices` | Bearer | Devices at `response.devices[]`. Fields: `id`, `serial_number`, `name`, `model`, `zones[]`, `temperature_range`, `temperature_scale` |
| GET | `/v1/sleep-schedules` | Bearer | Schedules at `response.schedules.{user_id}[]` (7 days). Also `today_sleep_schedule.{user_id}` |
| PUT | `/v1/sleep-schedules` | Bearer | Update schedule. Body: `{"schedules": [{"day": N, field: value}]}`. Partial updates work (only specified field changes). |
| POST | `/v1/sleep-configurations/user-away` | Bearer | Presence override. Body: `{"user_id": "...", "is_away": bool}`. Also powers the device down; prefer `/v1/devices/{id}/live` for pure power control. |
| PUT | `/v1/devices/{deviceId}` | Bearer | Update metadata (`name`, `orientation`, `timezone`). Partial updates accepted. |
| GET | `/v1/devices/{serial_number}/live` | Bearer | **Live runtime snapshot** (zones with `on`/`temp`, status, sensors, firmware). Path uses `serial_number`, NOT UUID. |
| PUT | `/v1/devices/{serial_number}/live` | Bearer | **Canonical power/temp primitive.** Path uses `serial_number`, NOT UUID (UUID returns `403 "Device not found"`). Body: `{"zones": [{"id": "zone_a", "on": bool, "temp": float}, ...]}`. Each zone requires `id` and at least one of `on`/`temp` (Celsius). |
| PUT | `/v1/devices/{serial_number}/live/zones/{zoneId}` | Bearer | Single-zone power/temp. Path uses `serial_number`. Body: `{on?, temp?}` with `minProperties: 1`. |
| POST | `/v1/devices/{deviceId}/action` | Bearer | Device action (quiet_mode, reboot, LED brightness, etc.). **No power action** — `DeviceAllowedAction` enum contains no on/off. Body: `{"action": "...", "value"?: ...}`. |
| POST | `/v1/devices/{deviceId}/activate` | Bearer | Pair device to account. Body: `{"model": "OSCT001-1"}`. |
| POST | `/v1/devices/{deviceId}/deactivate` | Bearer | Unpair device. |
| POST | `/v1/devices/{deviceId}/update` | Bearer | Trigger firmware update. |
| GET | `/v2/insights?from=&to=` | Bearer | NOT wrapped in `response`. Top-level: `{user_id, data: {date: {score, sessions[]}}, overview: {date: {score}}}` |

### Non-Working / Unverified Endpoints

| Path | Status | Notes |
|------|--------|-------|
| `/v1/sleep-configurations/devices` | **404** | Does not exist despite OpenAPI spec |
| `/v1/sleep-configurations/temperature` | Unverified | PUT to set temp — not tested against live API |
| `/v1/sleep-schedules?action=enable` | Unverified | Schedule enable/disable — body format `{"enabled": bool}` not confirmed |
| `/v1/session-state` | Returns onboarding state | `{patch_step, is_survey_complete, ...}` — NOT sleep session state |

### Real API Response Shapes

**Devices** — each device has:
- `id` (UUID), `serial_number`, `name`, `model` ("OSCT001-1"), `type` ("control_tower")
- `zones`: `[{id: "zone_a", user: {...}}, {id: "zone_b", user: {...}}]`
- `temperature_range`: `{min: 10, max: 45}` (Celsius)
- `temperature_scale.fahrenheit[]`: `{in: 50..113, out: 10..45}` mapping
- `temperature_scale.relative[]`: `{in: -10..+10, out: 10..45}` non-linear offset-to-Celsius mapping
- `orientation`, `timezone`, `permissions`, `default_zone_id`

**Schedules** — keyed by user_id, 7 entries (day 0-6):
- `bedtime`, `wakeup` (HH:mm strings)
- `bedtime_is_active`, `wakeup_is_active` (booleans)
- `bedtime_temp`, `wakeup_temp`, `phase_1_temp`, `phase_2_temp` (Celsius floats)
- `auto_turn_off`, `is_smart_temperature_active`
- `override_date`, `is_override_available`, `is_override_applied`

**Insights sessions** — each session has:
- `session_id`, `zone_id`, `is_in_progress`, `start_time`, `end_time`, `confidence`
- `sleep_summary`: `{time_asleep, deep_sleep, rem_sleep, light_sleep, awake_time}` (minutes)
- `heart_rate`: `{average, min, max, values[]}` (BPM)
- `breath_rate`: `{average, min, max, values[]}` (breaths/min)
- `hrv`: `{average, min, max, values[]}` (ms, often null)
- `movement`: `{total_seconds, movement_rate, left_bed_seconds, values[]}`
- `temperature`: `{values[]}` (Celsius floats, ~3 per minute)

### Key Gotchas

- Token fields are **snake_case** (`access_token`, NOT `accessToken`)
- Refresh response may be nested (`response.session`) or flat — handle both
- Token expiry uses `expires_at` Unix timestamp, NOT JWT parsing
- Insights endpoint (`/v2/insights`) does NOT wrap in `response` — it's top-level
- All other endpoints wrap data in `{"response": {...}, "success": true}`
- Temperature values throughout the API are in **Celsius**
- Device zones are `zone_a`/`zone_b`, not `left`/`right`
- Sleep session detection uses `is_in_progress` from insights, not `/v1/session-state`
- Device power state is read from each zone's `on`/`is_on` field (set via `PUT /v1/devices/{id}/live`); `set_user_away` affects the `user` field but is a separate presence override
- Temperature offsets (app-style -10 to +10) map **non-linearly** to absolute Celsius via `temperature_scale.relative` table

## Architecture

- **Polling**: `DataUpdateCoordinator` polls `/v1/devices`, `/v1/sleep-schedules`, and `/v2/insights` on a configurable interval (default 600s)
- **One-time data**: User profile fetched once in `_async_setup()`
- **Per-poll data**: Device list re-fetched each poll to detect away/present (power) state changes
- **Token persistence**: Refresh callback updates `config_entry.data` so tokens survive HA restarts
- **Error handling**: Each polled endpoint has independent try/except — one failing doesn't break the others. Auth errors (`OrionAuthError`) always raise `ConfigEntryAuthFailed` to trigger re-auth flow.
- **Auth flow**: Three-step config flow (pick method -> enter email/phone -> enter verification code) + re-auth support
- **Options flow**: Configurable `scan_interval` (60-3600s) and `insights_days` (1-30 days)
- **Temperature conversion**: `OrionBaseEntity` provides `_celsius_to_offset()` and `_offset_to_celsius()` using per-device lookup table (falls back to `DEFAULT_RELATIVE_TEMP_TABLE` in `const.py`)

### Data Flow

```
Config Flow (auth) --> tokens stored in config_entry.data
       |
       v
__init__.py creates OrionApiClient + OrionDataUpdateCoordinator
       |
       v
coordinator._async_setup() -- fetches user profile + devices (once)
       |
       v
coordinator._async_update_data() -- polls every N seconds:
  1. ensure_valid_token() (auto-refresh, persists via callback)
  2. list_devices()        --> coordinator.devices (away/present detection)
  3. OrionWebSocketManager.sync_to_serials() (start/stop per-device WS)
  4. get_live_device(serial) per device (skipped when WS is fresh)
  5. get_sleep_schedules() --> data["schedules"]
  6. get_insights(days=N)  --> data["insights"]
       |
       v
Per-device live WebSocket (wss://live.api1.orionbed.com/device/<serial>):
  - Pushes live_device.snapshot on connect, live_device.update on every
    state change (+ idle heartbeat every ~2s)
  - Coordinator._handle_ws_message merges payload into live_devices
    and calls async_set_updated_data() so entities refresh immediately
  - Timeline field is stored at data["ws_timelines"][device_id]
       |
       v
Entities read from coordinator:
  - Climate: schedule (target temp, HVAC mode) + session (current temp)
  - Number: per-phase app-style temperature offsets (-10..+10)
  - Sensors: insights sessions + schedule + overview scores
             + per-topper-sensor live HR/BR/status (from WS)
  - Binary sensors: session.is_in_progress
                    + per-topper-sensor occupancy (from WS)
  - Switches: device zones (power) + user-away (away mode)
              + schedule.bedtime_is_active
  - Diagnostic sensors: per-device WS connection state
                        + per-topper-sensor raw status_text
```

## Entities

| Platform | Entity | Key / unique_id suffix | Data Source |
|----------|--------|----------------------|-------------|
| Climate | Bed Climate | `_climate` | Target temp from `today_sleep_schedule.bedtime_temp`, current from latest session `temperature.values[-1]` |
| Sensor | Sleep Score | `_sleep_score` | `insights.overview.{latest_date}.score` with `quality_rating` extra attr |
| Sensor | Total Sleep Time | `_total_sleep_time` | `session.sleep_summary.time_asleep` (formatted as "Xh Ym") |
| Sensor | Deep Sleep Time | `_deep_sleep_time` | `session.sleep_summary.deep_sleep` |
| Sensor | REM Sleep Time | `_rem_sleep_time` | `session.sleep_summary.rem_sleep` |
| Sensor | Light Sleep Time | `_light_sleep_time` | `session.sleep_summary.light_sleep` |
| Sensor | Awake Time | `_awake_time` | `session.sleep_summary.awake_time` |
| Sensor | Heart Rate Average | `_heart_rate_avg` | `session.heart_rate.average` + min/max/range extra attrs |
| Sensor | Breath Rate | `_breath_rate` | `session.breath_rate.average` + min/max/range extra attrs |
| Sensor | HRV | `_hrv` | `session.hrv.average` + min/max extra attrs |
| Sensor | Body Movement Rate | `_body_movement_rate` | `session.movement.movement_rate` |
| Sensor | Restless Time | `_restless_time` | `session.movement.total_seconds` (formatted as "Xm Ys") |
| Sensor | Bedtime | `_bedtime` | `today_sleep_schedule.bedtime` (HH:mm) |
| Sensor | Wake-up Time | `_wakeup_time` | `today_sleep_schedule.wakeup` |
| Sensor | Schedule Duration | `_schedule_duration` | Calculated from bedtime/wakeup (handles overnight) |
| Sensor | Bedtime Temperature | `_bedtime_temp` | `today_sleep_schedule.bedtime_temp` + phase/smart temp extra attrs |
| Sensor | Wake-up Temperature | `_wakeup_temp` | `today_sleep_schedule.wakeup_temp` |
| Sensor | Current Temp Offset | `_current_temp_offset` | Latest session `temperature.values[-1]` converted to app-style offset. **Registered twice (pre-existing bug), see Known Issues.** |
| Sensor (diag) | Live Connection | `_websocket_state` | WS connection state (`connecting`/`connected`/`reconnecting`/`device_offline`/`auth_failed`/`stopped`) plus `seconds_since_last_message` extra attr |
| Sensor | Sensor 1/2 Heart Rate | `_sensorN_live_heart_rate` | WS `status.sensors.sensorN.heart_rate` (bpm). `0` (empty bed) and `255` (no reading yet) both mapped to `None`. |
| Sensor | Sensor 1/2 Breath Rate | `_sensorN_live_breath_rate` | WS `status.sensors.sensorN.breath_rate` (br/min). Same sentinel handling. |
| Sensor (diag) | Sensor 1/2 Status | `_sensorN_sensor_status` | Raw `status_text`: observed `left_bed` (empty) and `normal` (occupied). |
| Binary Sensor | Sleep Session Active | `_session_active` | `session.is_in_progress` (shows "Asleep" / "Not asleep") |
| Binary Sensor | Sensor 1/2 On Bed | `_sensorN_on_bed` | Occupancy device class. `status_text != "left_bed"`. Flips within ~2s via WS push. |
| Switch | Power | `_power` | On = all zones on, Off = all zones off. Uses `PUT /v1/devices/{id}/live` (canonical power primitive). State read from each zone's `on`/`is_on` field. |
| Switch | Away Mode | `_away_mode` | On = user marked away, Off = user present. State read from `zones[*].user` (null across all zones = away). `POST /v1/sleep-configurations/user-away`. Returns `400 "User has no previous device to return to"` on no-op toggle — swallowed in the switch. |
| Switch | Sleep Schedule | `_sleep_schedule` | `today_sleep_schedule.bedtime_is_active`. Toggle via `update_sleep_schedule`. |
| Number | Bedtime Temperature Offset | `_bedtime_temp_offset` | App-style -10..+10 slider. Reads `today_sleep_schedule.bedtime_temp`, converts to offset via per-device relative table; writes back via `PUT /v1/sleep-schedules` on today's day-of-week. |
| Number | Asleep Phase 1 Offset | `_phase_1_temp_offset` | As above, `phase_1_temp` field. |
| Number | Asleep Phase 2 Offset | `_phase_2_temp_offset` | As above, `phase_2_temp` field. |
| Number | Wake Up Temperature Offset | `_wakeup_temp_offset` | As above, `wakeup_temp` field. |

**Per device: 1 climate + 4 number + 24 sensors + 3 binary sensors + 3 switches = 35 entities**

- 24 sensors = 11 insights + 5 schedule + 1 current-temp-offset + 1 live-connection + 6 per-sensor live (2× HR + 2× BR + 2× diag status_text). The current-temp-offset is accidentally registered twice (same unique_id, HA keeps one) — the 24 count reflects the logical set.
- 4 number sliders: one per schedule-phase temperature offset (bedtime / phase_1 / phase_2 / wakeup).
- 3 binary sensors: Sleep Session Active + 2× On Bed (sensor1/sensor2).
- 3 switches: Power, Away Mode, Sleep Schedule.

### Sensor Implementation Notes

- Duration sensors (total sleep, deep sleep, etc.) deliberately avoid `device_class=DURATION` because HA would override entity names
- Sleep score has special handling: reads from `insights.overview` (not sessions) and adds `quality_rating` extra attribute ("Excellent" >= 90, "Good" >= 80, "Fair" >= 60, "Poor" < 60)
- Temperature offset conversion uses per-device `temperature_scale.relative` lookup table, non-linear mapping
- Heart rate and breath rate sensors include min/max/range as extra state attributes

## API Client (`api.py`)

### Exception Hierarchy
- `OrionApiError` — base for all API errors
- `OrionAuthError(OrionApiError)` — 401 / invalid tokens
- `OrionConnectionError(OrionApiError)` — network failures (`aiohttp.ClientError`)

### Token Management
- `_token_expired(margin_seconds=60)` — checks `time.time() + 60` against `expires_at`
- `ensure_valid_token()` — auto-refreshes if expired
- `_refresh_tokens()` — handles both nested (`response.session`) and flat response shapes
- `set_token_refresh_callback(callback)` — called after successful refresh to persist tokens

### Action Methods
| Method | Endpoint | Status |
|--------|----------|--------|
| `set_temperature(device_id, temperature, zone_id)` | `PUT /v1/sleep-configurations/temperature` | **Unverified** (prefer `update_live_device_zone[s]`) |
| `set_user_away(user_id, is_away)` | `POST /v1/sleep-configurations/user-away` | Working (used by away-mode switch; presence override) |
| `update_device(device_id, **fields)` | `PUT /v1/devices/{deviceId}` | Metadata updates (name/orientation/timezone) |
| `update_live_device_zones(device_id, zones)` | `PUT /v1/devices/{deviceId}/live` | **Canonical power primitive** (used by power switch) |
| `update_live_device_zone(device_id, zone_id, on=, temp=)` | `PUT /v1/devices/{deviceId}/live/zones/{zoneId}` | Per-zone power/temp |
| `device_action(device_id, action, value=)` | `POST /v1/devices/{deviceId}/action` | quiet_mode/reboot/etc. — NOT for power |
| `activate_device(device_id, model)` | `POST /v1/devices/{deviceId}/activate` | Pair device |
| `deactivate_device(device_id)` | `POST /v1/devices/{deviceId}/deactivate` | Unpair device |
| `trigger_firmware_update(device_id)` | `POST /v1/devices/{deviceId}/update` | Firmware update |
| `update_schedule_temperature(day, field, celsius)` | `PUT /v1/sleep-schedules` | Partial updates verified |
| `update_sleep_schedule(schedule_data, action)` | `PUT /v1/sleep-schedules` | **Unverified** for enable/disable action |

## Testing

Run `orion_info.py` to verify API connectivity and response shapes:
```bash
python orion_info.py --email user@example.com
python orion_info.py --phone 15132015808
```
Tokens cache to `~/.orion_tokens.json`. Use `--relogin` to force fresh auth.

Additional `orion_info.py` flags:
- `--insights-days N` — number of days of insights to fetch
- `--set-away` / `--set-present` — toggle device power, then re-fetch devices/schedules to show changes
- `--power-on` / `--power-off` — probe `PUT /v1/devices/{ident}/live` against both `id` and `serial_number`
- `--websocket [--ws-duration N]` — open `/device/<serial>?token=<JWT>` and log every frame for N seconds (default 60)
- `--ws-scenario` — open the WebSocket and drive a scripted sequence of REST edits (zone on/off, temp low/high, bulk on/off, user-away) while logging frames; restores the original zone state at the end. Use this to re-verify the event taxonomy against the live server.

## WebSocket — Live Device Data

Validated against the live server with `orion_info.py --ws-scenario`.

### Connection

```
wss://live.api1.orionbed.com/device/<serial_number>?token=<JWT>
```

- Path uses the device's **`serial_number`**, NOT its UUID `id` (UUID returns 404 `{"error":"Not Found","message":"Device not found"}`).
- JWT is passed as a `token` query parameter.
- Cloudflare negotiates HTTP/2 by default which breaks the WS upgrade — the SSL context **must force ALPN to `http/1.1`**.
- Working User-Agent: `okhttp/4.12.0`.
- **No client-side handshake**. The server pushes `live_device.snapshot` immediately after the Upgrade completes, then `live_device.update` on state changes and approximately every 2s as an idle refresh.
- Close code `1001` on clean client shutdown.
- On 401 during upgrade, refresh via `POST /v1/auth/refresh` and reconnect with the new token.

### Event Taxonomy (exhaustive as of last capture)

| `type` | When | Notes |
|---|---|---|
| `live_device.snapshot` | Once, immediately after connect | Full state |
| `live_device.update` | On every REST mutation to `/v1/devices/{serial}/live[/zones/{zone}]` or `/v1/sleep-configurations/user-away`, plus ~every 2s as an idle refresh | Same payload shape as snapshot; may include a `timeline` array of today's schedule actions |

Both use the envelope `{"type": <event>, "payload": {...}}`. `set_user_away` does **not** emit a distinct event type — it produces another `live_device.update` with zones powered accordingly.

### Payload Shape (shared between snapshot and update)

```text
payload.serial_number         string
payload.model                 e.g. "OSCT001-1"
payload.zones[]               setpoints (user intent): {id, temp (°C), on}
payload.led_brightness        int 0-100
payload.water_fill            string (observed "unknown")
payload.is_in_water_fill_mode bool
payload.status.online         bool
payload.status.firmware       {cb, ib}
payload.status.firmware_update {workflow_id, started_at, updated_at, in_progress,
                                current_step, completed_at, result}
payload.status.pending_update {is_available}
payload.status.network        {last_seen, name, ip, rssi, uptime, mac}
payload.status.safety         {error, error_codes[], error_descriptions[]}
payload.status.zones[]        measured: {id, temp (°C), thermal_state}
payload.status.sensors.sensor1, sensor2
                              {heart_rate, breath_rate, status, status_text,
                               sign_of_asleep, sign_of_wake_up, timestamp,
                               uptime, is_working, firmware_version,
                               hardware_version}
payload.timeline[]            only on update; today's scheduled actions:
                              {id, user_id, label (bedtime|phase_1|phase_2|
                               wake_up|turn_off), scheduled_time, action:
                               {zones:[...]}, created_at}
```

Notable:
- `payload.zones[].temp` is the **setpoint**. The **measured** zone temperature lives at `payload.status.zones[].temp`.
- `status.zones[].thermal_state` was only observed as `"standby"`; heating/cooling values are plausible but unobserved.
- `sensors.sensor*.status_text` observed values: `"left_bed"` (empty bed, HR=BR=0) and `"normal"` (occupied, realistic HR/BR). The topper also reports HR=BR=255 as a "no reading yet" sentinel in the first ~2s after someone sits down. Other values hinted at by the app strings (e.g. sitting/asleep/error) are plausible but unobserved.
- `sensors.sensor*.sign_of_asleep` / `sign_of_wake_up` only ever observed as `1`; likely edge triggers that momentarily take another value during stage transitions (unconfirmed — a full sleep session hasn't been captured).

### Events NOT Observed (may exist, were not triggered)

- Distinct session-start / session-end events (likely still only available via `/v2/insights` polling)
- Device-offline event (device was online throughout the capture)
- quiet_mode / reboot action responses
- Firmware-update-in-progress transitions
- Water-fill-mode transitions

## Known Issues

- **Duplicate entity**: `OrionCurrentTempOffsetSensor` is appended twice per device in `sensor.py:351-352` (same `unique_id`, HA will reject or warn about the second)
- **Unused translations**: `bed_climate_left` and `bed_climate_right` defined in strings.json but no entities use them

## Known Limitations / Future Work

- `set_temperature` endpoint not verified against live API
- Schedule enable/disable (`PUT /v1/sleep-schedules?action=enable`) not verified
- `async_set_hvac_mode(OFF)` and `async_turn_off()` on climate entity are no-ops (schedule-based control only)
- Firmware versions are not exposed as dedicated entities yet (available in the WS payload at `status.firmware.{cb,ib}` and on each sensor block's `firmware_version` — plumb through if surfacing them becomes useful)
- HRV values frequently null in real data
- No way to start/stop sleep sessions via API
- Zone splitting/merging not supported
- Guest user management not supported
- `OrionPowerSwitch` and `OrionScheduleSwitch` don't catch API errors — they propagate to the HA UI as failed-action notifications. `OrionAwayModeSwitch` specifically swallows the `400 "User has no previous device to return to"` that the server returns on a no-op toggle.
- Topper sensor1 ↔ sensor2 to zone_a ↔ zone_b mapping is unverified — entities are named per sensor rather than per side until a split-occupancy capture confirms the mapping
