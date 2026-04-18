# Orion Sleep - Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

Custom [Home Assistant](https://www.home-assistant.io/) integration for the **Orion Sleep** smart mattress topper. Control bed temperature, react to occupancy in real time, monitor sleep metrics, and manage sleep schedules — all from your Home Assistant dashboard.

## Features

- **Live WebSocket stream** — Temperature, power, and sensor readings update in realtime when the bed or the Orion app changes anything; no need to wait for the next poll.
- **Bed occupancy** — Per-topper-sensor binary sensors track who is on the bed. Latency varies; expect ~30 s to 1 minute after sitting down or leaving before the sensor flips (the topper itself is slow to decide).
- **Live heart rate and breath rate** — Per-sensor realtime readings from the topper (distinct from the post-session averages).
- **Climate control** — Target bed temperature per-zone, with the current measured temperature pulled from the latest session.
- **Power and presence switches** — One-click power via the canonical `/v1/devices/{serial}/live` endpoint, plus an Away Mode switch that reads the authoritative presence signal from `zones[*].user`.
- **Sleep insight sensors** — Sleep score, HRV, heart rate, breath rate, sleep-stage durations (awake / light / deep / REM), total time asleep, restless time, and body-movement rate for your most recent session.
- **Schedule sensors and sliders** — Today's bedtime, wake-up time, duration, and target temperatures, plus Number sliders for adjusting the four schedule-phase temperature offsets (-10 … +10, app-style).
- **Session tracking** — Binary sensor showing whether a sleep session is currently in progress.
- **Diagnostic entity** — Live-connection state sensor (`connecting` / `connected` / `reconnecting` / `device_offline` / `auth_failed`), with the seconds-since-last-frame exposed as an attribute.
- **Passwordless auth with automatic refresh** — Sign in with the same email or phone + verification code flow as the Orion app. Tokens are refreshed automatically; you are prompted to re-authenticate only if the refresh token itself is revoked.
- **Redacted diagnostics** — `Download diagnostics` produces a debug bundle with tokens, identifiers, and network PII stripped.

## Installation

### HACS (Recommended)

1. Make sure [HACS](https://hacs.xyz/) is installed in your Home Assistant instance.

2. Click the button below to add this repository:

   [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=tdickman&repository=home-assistant-orion-integration&category=integration)

   Or manually add the custom repository: go to **HACS > Integrations > three-dot menu > Custom repositories**, paste `https://github.com/tdickman/home-assistant-orion-integration` and select **Integration** as the category.

3. Search for "Orion Sleep" in HACS and download it.

4. Restart Home Assistant.

### Manual

1. Copy the `custom_components/orion_sleep` directory into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.

## Configuration

After installation, add the integration:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=orion_sleep)

Or go to **Settings > Devices & Services > Add Integration** and search for "Orion Sleep".

### Setup steps

1. Choose whether to sign in with **email** or **phone**.
2. Enter your Orion Sleep account email or phone number. A verification code is sent the same way as it is for the Orion app.
3. Enter the verification code to complete setup.

### Options

After setup, you can configure:

| Option | Default | Description |
|---|---|---|
| Polling interval | 600 s (10 min) | How often to fetch data from the Orion REST API (60 – 3600 s). The WebSocket stream runs continuously and is independent of this interval. |
| Insights days | 7 | Number of days of sleep history to retrieve (1 – 30) |

Go to **Settings > Devices & Services > Orion Sleep > Configure** to change these.

## Real-time updates

In addition to the REST poll, the integration opens one WebSocket per device to `wss://live.api1.orionbed.com/device/<serial_number>` and merges every `live_device.snapshot` / `live_device.update` frame into the coordinator state. This means:

- The Power switch and Bed Climate reflect app-side changes in realtime.
- Per-sensor vitals (heart rate, breath rate) update continuously while occupied.
- Occupancy binary sensors follow the topper's own classification, which can take ~30 s to 1 minute to decide someone has sat down or left. The underlying vitals still update in realtime.
- The integration will gracefully reconnect with exponential backoff if the stream drops, and automatically refresh the JWT before reconnecting on 401.

The live connection is fully automatic — there is no option to disable it today. Its health is exposed by the **Live Connection** diagnostic sensor per device.

## Entities

One device is created per paired topper. Each device exposes the entities listed below. Entity names below use the integration's default translation strings.

### Climate

| Entity | Description |
|---|---|
| Bed Climate | Target temperature read from today's schedule, current temperature from the latest session's most-recent sample. HVAC mode reflects whether a session is in progress. |

### Switches

| Entity | Description |
|---|---|
| Power | All zones on/off via `PUT /v1/devices/{serial}/live`. State derived from each zone's `on` field. |
| Away Mode | Marks you present/away via `POST /v1/sleep-configurations/user-away`. State derived from whether any zone carries a populated `user` object (the authoritative presence signal). |
| Sleep Schedule | Enable/disable today's bedtime action via `PUT /v1/sleep-schedules`. |

### Numbers (temperature-offset sliders)

App-style offsets (-10 to +10) that map non-linearly to Celsius using the device's `temperature_scale.relative` lookup table. Each slider writes back to today's schedule.

- Bedtime Temperature Offset
- Asleep Phase 1 Offset
- Asleep Phase 2 Offset
- Wake Up Temperature Offset

### Sensors — sleep insights (latest completed session)

| Entity | Unit | Source |
|---|---|---|
| Sleep Score | points | `insights.overview[latest].score` with a `quality_rating` attribute (Excellent / Good / Fair / Poor) |
| Total Sleep Time | formatted `Xh Ym` | `sleep_summary.time_asleep` |
| Deep Sleep | formatted `Xh Ym` | `sleep_summary.deep_sleep` |
| REM Sleep | formatted `Xh Ym` | `sleep_summary.rem_sleep` |
| Light Sleep | formatted `Xh Ym` | `sleep_summary.light_sleep` |
| Awake Time | formatted `Xh Ym` | `sleep_summary.awake_time` |
| Heart Rate | bpm | `heart_rate.average` plus `min` / `max` / `range` attributes |
| Breath Rate | breaths/min | `breath_rate.average` plus `min` / `max` / `range` attributes |
| HRV | ms | `hrv.average` plus `min` / `max` attributes (often null in real data) |
| Body Movement Rate | /hr | `movement.movement_rate` |
| Restless Time | formatted `Xm Ys` | `movement.total_seconds` |

### Sensors — today's schedule

- Bedtime (HH:mm)
- Wake Up Time (HH:mm)
- Schedule Duration (formatted, handles overnight)
- Bedtime Temperature (°C) with phase-1 / phase-2 temp and smart-temperature attributes
- Wake Up Temperature (°C)
- Current Temperature Offset (app-style -10 … +10, computed from the latest session sample via the per-device non-linear lookup table)

### Sensors — live (WebSocket-driven)

Two in-topper sensors (`sensor1`, `sensor2`) report continuously while the device is online. The mapping between these and the physical left / right side of the bed has not been verified, so entities are named per sensor.

| Entity | Unit | Source |
|---|---|---|
| Sensor 1 Heart Rate | bpm | `status.sensors.sensor1.heart_rate` |
| Sensor 2 Heart Rate | bpm | `status.sensors.sensor2.heart_rate` |
| Sensor 1 Breath Rate | br/min | `status.sensors.sensor1.breath_rate` |
| Sensor 2 Breath Rate | br/min | `status.sensors.sensor2.breath_rate` |

The raw `status_text`, `is_working`, `firmware_version`, and `hardware_version` from each sensor are exposed as extra state attributes on the heart-rate and breath-rate entities. A reading of `0` (empty bed) or `255` (no reading yet) is reported as `unknown` — both are server-side sentinels, not real vitals.

### Sensors — diagnostic

| Entity | Source |
|---|---|
| Live Connection | WebSocket state: `stopped` / `connecting` / `connected` / `reconnecting` / `device_offline` / `auth_failed`, with `seconds_since_last_message` as an attribute. |
| Sensor 1 Status | Raw `status_text` from topper sensor 1 (observed values: `left_bed`, `normal`). |
| Sensor 2 Status | Raw `status_text` from topper sensor 2. |

### Binary sensors

| Entity | Device class | Description |
|---|---|---|
| Sleep Session | — | `insights.session.is_in_progress`. Rendered as "Asleep" / "Not asleep". |
| Sensor 1 On Bed | Occupancy | `sensor1.status_text != "left_bed"`. Driven by the topper's own classification, which can take ~30 s to 1 minute to react to someone sitting down or leaving. |
| Sensor 2 On Bed | Occupancy | `sensor2.status_text != "left_bed"`. Same latency caveat as sensor 1. |

## Troubleshooting

- **Re-authentication** — If both the access and refresh tokens expire or are revoked, Home Assistant will raise a re-auth flow; follow the prompts to receive and enter a new verification code.
- **Away Mode switch** — If you toggle Away Mode and the Orion app shows you in Home mode (or vice-versa), the device was probably in an already-matching state. The integration swallows the specific `400 "User has no previous device to return to"` error that the server returns on a redundant toggle and simply logs it at `debug`.
- **Live Connection stuck on `reconnecting`** — Typically indicates a network problem reaching `live.api1.orionbed.com`. The client falls back to REST polling so the rest of the integration keeps working; restart HA or check your outbound HTTPS / WSS connectivity.
- **Logs** — Go to **Settings > System > Logs** and filter for `orion_sleep`. For more detail, add this to `configuration.yaml`:

  ```yaml
  logger:
    default: warning
    logs:
      custom_components.orion_sleep: debug
  ```

- **Diagnostics** — Use **Settings > Devices & Services > Orion Sleep > three-dot menu > Download diagnostics** to generate a debug bundle. Access tokens, refresh tokens, user identifiers, names, serial numbers, IP and MAC addresses are automatically redacted.

## Notes and limitations

- Writing to `PUT /v1/sleep-configurations/temperature` has not been verified against the live API; climate `set_temperature` and the Number sliders use `PUT /v1/sleep-schedules` instead, which is confirmed.
- Home Assistant's climate `async_turn_off` / `async_set_hvac_mode(OFF)` are no-ops for Bed Climate — the underlying system is schedule-driven. Use the **Power** switch to actually turn the device off.
- HRV values are frequently `null` in real data; the HRV sensor will then report as `unknown`.
- Starting and stopping sleep sessions is not supported by the API.
- Zone splitting / merging and guest-user management are not exposed.

## License

This project is not affiliated with or endorsed by Orion Longevity Inc.
