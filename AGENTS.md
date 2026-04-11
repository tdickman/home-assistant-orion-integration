# Orion Sleep - Home Assistant HACS Integration

## Project Overview

HACS-compatible Home Assistant custom integration for the **Orion Sleep** smart mattress topper. Cloud-connected bed temperature control with per-zone support, sleep tracking (heart rate, breath rate, HRV, sleep stages), and sleep scheduling.

## Repository Structure

```
home-assistant-orion-integration/
├── hacs.json                          # HACS repo metadata
├── README.md                          # User-facing install/usage docs
├── openapi.yaml                       # OpenAPI 3.1 spec (reverse-engineered, NOT fully accurate)
├── orion_info.py                      # Working CLI script — ground truth for API behavior
├── custom_components/
│   └── orion_sleep/
│       ├── __init__.py                # async_setup_entry / async_unload_entry
│       ├── manifest.json              # HA integration manifest
│       ├── const.py                   # DOMAIN, config keys, defaults
│       ├── api.py                     # Async aiohttp API client
│       ├── coordinator.py             # DataUpdateCoordinator + data helpers
│       ├── config_flow.py             # Three-step auth flow + options flow
│       ├── entity.py                  # Base entity with DeviceInfo
│       ├── climate.py                 # Bed temperature control
│       ├── sensor.py                  # Sleep insight sensors (11 sensors)
│       ├── binary_sensor.py           # Sleep session active
│       ├── switch.py                  # Sleep schedule enable/disable
│       ├── diagnostics.py             # Diagnostics with PII redaction
│       ├── strings.json               # UI translations
│       └── brand/                     # Integration icon (96px + 180px)
```

## Critical: API Behavior vs OpenAPI Spec

The `openapi.yaml` was reverse-engineered from the Android app bytecode. It has significant inaccuracies. **Always trust `orion_info.py` and the notes below over the OpenAPI spec.**

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
| GET | `/v2/insights?from=&to=` | Bearer | NOT wrapped in `response`. Top-level: `{user_id, data: {date: {score, sessions[]}}, overview: {date: {score}}}` |

### Non-Working / Unverified Endpoints

| Path | Status | Notes |
|------|--------|-------|
| `/v1/sleep-configurations/devices` | **404** | Does not exist despite OpenAPI spec |
| `/v1/sleep-configurations/temperature` | Unverified | PUT to set temp — not tested against live API |
| `/v1/sleep-configurations/user-away` | Unverified | Removed from integration |
| `/v1/session-state` | Returns onboarding state | `{patch_step, is_survey_complete, ...}` — NOT sleep session state |

### Real API Response Shapes

**Devices** — each device has:
- `id` (UUID), `serial_number`, `name`, `model` ("OSCT001-1"), `type` ("control_tower")
- `zones`: `[{id: "zone_a", user: {...}}, {id: "zone_b", user: {...}}]`
- `temperature_range`: `{min: 10, max: 45}` (Celsius)
- `temperature_scale.fahrenheit[]`: `{in: 50..113, out: 10..45}` mapping
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

## Architecture

- **Polling**: `DataUpdateCoordinator` polls `/v1/sleep-schedules` and `/v2/insights` on a configurable interval (default 600s)
- **One-time data**: User profile and device list fetched in `_async_setup()`
- **Token persistence**: Refresh callback updates `config_entry.data` so tokens survive HA restarts
- **Error handling**: Each polled endpoint has independent try/except — one failing doesn't break the others
- **Auth flow**: Three-step config flow (pick method -> enter email/phone -> enter verification code)

## Entities

| Platform | Entity | Data Source |
|----------|--------|-------------|
| Climate | Bed Climate | Target temp from `today_sleep_schedule.bedtime_temp`, current from latest session `temperature.values[-1]` |
| Sensor | Sleep Score | `insights.overview.{latest_date}.score` |
| Sensor | Total Sleep Time | `session.sleep_summary.time_asleep` |
| Sensor | Deep/REM/Light/Awake Time | `session.sleep_summary.*` |
| Sensor | Heart Rate Average | `session.heart_rate.average` |
| Sensor | Breath Rate | `session.breath_rate.average` |
| Sensor | HRV | `session.hrv.average` |
| Sensor | Body Movement Rate | `session.movement.movement_rate` |
| Sensor | Restless Time | `session.movement.total_seconds` |
| Binary Sensor | Sleep Session Active | `session.is_in_progress` |
| Switch | Sleep Schedule | `today_sleep_schedule.bedtime_is_active` |

## Testing

Run `orion_info.py` to verify API connectivity and response shapes:
```bash
python orion_info.py --email user@example.com
python orion_info.py --phone 15132015808
```
Tokens cache to `~/.orion_tokens.json`. Use `--relogin` to force fresh auth.

## Known Limitations / Future Work

- `set_temperature` endpoint not verified against live API
- Schedule enable/disable (`PUT /v1/sleep-schedules`) not verified
- No WebSocket support (WS URL/protocol not documented)
- No firmware version exposed (not in device response)
- HRV values frequently null in real data
- No way to start/stop sleep sessions via API
- Zone splitting/merging not supported
- Guest user management not supported
