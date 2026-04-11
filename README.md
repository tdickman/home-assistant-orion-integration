# Orion Sleep - Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

Custom [Home Assistant](https://www.home-assistant.io/) integration for the **Orion Sleep** smart mattress topper. Control bed temperature, monitor sleep metrics, and manage sleep schedules — all from your Home Assistant dashboard.

## Features

- **Climate control** — Set and monitor bed temperature per-zone (supports split left/right zones)
- **Sleep insight sensors** — Sleep score, HRV, breath rate, body movement, sleep stages (awake/light/deep/REM), and total sleep time
- **Session tracking** — Binary sensor indicating whether a sleep session is currently active
- **Sleep schedule** — Switch to enable/disable your configured sleep schedule
- **Automatic token refresh** — Passwordless auth with automatic session management
- **Diagnostics** — Built-in diagnostics support for troubleshooting

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

1. Enter your Orion Sleep account email or phone number.
2. A verification code will be sent to you (same as the Orion app login).
3. Enter the verification code to complete setup.

### Options

After setup, you can configure:

| Option | Default | Description |
|---|---|---|
| Polling interval | 600s (10 min) | How often to fetch data from the Orion API (60–3600s) |
| Insights days | 7 | Number of days of sleep history to retrieve (1–30) |

Go to **Settings > Devices & Services > Orion Sleep > Configure** to change these.

## Entities

### Climate

One climate entity per bed zone. If your bed has split zones enabled, you get separate left and right entities.

- **HVAC modes**: Heat/Cool (device auto-regulates to target), Off
- **Preset modes**: None, Away
- **Temperature control**: Set target temperature in Fahrenheit

### Sensors

| Sensor | Unit | Description |
|---|---|---|
| Sleep Score | points | Overall sleep quality score |
| HRV | ms | Heart rate variability |
| Breath Rate | breaths/min | Average breathing rate |
| Body Movement Rate | — | Movement index |
| Restless Time | min | Time spent restless |
| Times Left Bed | times | Number of times you left the bed |
| Total Sleep Time | min | Total time asleep |
| Awake Time | min | Time in awake stage |
| Light Sleep Time | min | Time in light sleep |
| Deep Sleep Time | min | Time in deep sleep |
| REM Sleep Time | min | Time in REM sleep |

### Binary Sensor

| Sensor | Description |
|---|---|
| Sleep Session Active | Whether a sleep session is currently in progress |

### Switch

| Switch | Description |
|---|---|
| Sleep Schedule | Enable or disable the configured sleep schedule |

## Troubleshooting

- If authentication expires, Home Assistant will prompt you to re-authenticate through the UI.
- Check **Settings > System > Logs** and filter for `orion_sleep` to see integration logs.
- Use **Settings > Devices & Services > Orion Sleep > three-dot menu > Download diagnostics** to generate a debug report (sensitive data is automatically redacted).

## License

This project is not affiliated with or endorsed by Orion Longevity Inc.
