# Orion Sleep - Home Assistant HACS Integration Implementation Plan

## Overview

Build a HACS-compatible Home Assistant custom integration for the **Orion Sleep** smart
mattress topper. The device is a cloud-connected bed temperature control system with
per-side zones, sleep tracking (HRV, breath rate, sleep stages), and sleep scheduling.

**Source files in this repo:**
- `openapi.yaml` — Full OpenAPI 3.1 spec for the Orion Sleep REST API (reverse-engineered from the Android app)
- `orion_info.py` — Working Python CLI script that authenticates and fetches data (use as ground truth for API behavior)

---

## Critical Details from orion_info.py (Ground Truth)

The OpenAPI spec was reverse-engineered from decompiled bytecode. The actual working
`orion_info.py` script reveals important differences from the spec. **Always trust
orion_info.py over the OpenAPI spec for field names and response shapes.**

### API Base URL
```
https://api1.orionbed.com
```

### Authentication Flow (Passwordless)

**Step 1: Request verification code**
```
POST /v1/auth/code
Content-Type: application/json

Body (email login):  {"email": "user@example.com"}
Body (phone login):  {"phone": "15132015808"}

Response 200: {"success": true}  (code sent via SMS or email)
```

**Step 2: Verify code and get tokens**
```
POST /v1/auth/verify
Content-Type: application/json

Body: {"code": "123456", "email": "user@example.com"}
  or: {"code": "123456", "phone": "15132015808"}

Response 200 (ACTUAL shape — nested structure):
{
  "response": {
    "session": {
      "access_token": "...",     // NOTE: snake_case, NOT camelCase
      "refresh_token": "...",    // NOTE: snake_case
      "expires_at": 1712345678   // Unix timestamp (seconds)
    },
    "user": { ... }
  },
  "success": true
}
```

**IMPORTANT**: The verify response nests tokens inside `response.session`. The token
field names are `access_token` and `refresh_token` (snake_case), NOT `accessToken` /
`refreshToken` as the OpenAPI spec suggests. There is also an `expires_at` Unix
timestamp — use this instead of JWT decoding.

**Step 3: Refresh tokens**
```
POST /v1/auth/refresh
Content-Type: application/json

Body: {"refresh_token": "..."}   // NOTE: snake_case key

Response 200 (ACTUAL shape — may be nested like verify):
{
  "response": {
    "session": {
      "access_token": "...",
      "refresh_token": "...",
      "expires_at": 1712345678
    }
  }
}
// OR top-level (code handles both):
{
  "access_token": "...",
  "refresh_token": "...",
  "expires_at": 1712345678
}
```

The refresh endpoint may return the session either nested under `response.session`
or at the top level. The API client must handle both cases (see `orion_info.py` line 155).

### Token Expiry Check
```python
# orion_info.py uses expires_at (Unix timestamp), NOT JWT decoding
def _token_expired(session: dict, margin_seconds: int = 60) -> bool:
    expires_at = session.get("expires_at", 0)
    return time.time() + margin_seconds >= expires_at
```

### Request Headers
```python
headers = {"Content-Type": "application/json"}
if token:
    headers["Authorization"] = f"Bearer {token}"
```

### Data Endpoints Used by orion_info.py
All require `Authorization: Bearer <access_token>` header.

| Endpoint | Method | Purpose |
|---|---|---|
| `/v1/auth/me` | GET | Current user profile |
| `/v1/devices` | GET | List user's Orion devices |
| `/v1/session-state` | GET | Current sleep session state (active/inactive) |
| `/v1/sleep-schedules` | GET | Sleep schedule configuration |
| `/v2/insights?from=YYYY-MM-DD&to=YYYY-MM-DD` | GET | Sleep insights for date range |

### Additional Endpoints from OpenAPI Spec (not in orion_info.py but needed)

| Endpoint | Method | Purpose | Needed For |
|---|---|---|---|
| `/v1/sleep-configurations/devices` | GET | Sleep config + temperature data for all devices | Climate entity (current/target temp, zones) |
| `/v1/sleep-configurations/temperature` | PUT | Set target temperature | Climate entity set_temperature |
| `/v1/sleep-configurations/user-away` | POST | Mark user as away | Climate preset mode |
| `/v1/sleep-schedules` | PUT | Update sleep schedule (with `?action=` query param) | Switch entity |

### Temperature Control (PUT /v1/sleep-configurations/temperature)
```json
{
  "deviceId": "device-uuid",
  "temperature": 72.0,
  "side": "left"          // only if split zones
}
```
Response: full SleepConfiguration object.

---

## Architecture Decisions

| Decision | Choice | Rationale |
|---|---|---|
| HVAC Mode | `HEAT_COOL` + `OFF` | Device heats and cools to target. User sets target temp, device figures out direction. |
| Polling Interval | Default 10 minutes, user-configurable | User preference. Configurable via options flow. |
| WebSocket | Deferred (polling only) | WS URL/protocol not documented. Keep v1 simple. |
| Sleep Insights | Full set of sensors | HRV, breath rate, sleep score, body movement, stages, total sleep time. |
| HTTP Client | `aiohttp` via HA's shared session | Required for async HA. Replace `requests` from orion_info.py. |
| Token Storage | `config_entry.data` | HA-managed persistence. No file-based caching. |
| GitHub URLs | Placeholder `YOUR_USERNAME` | Replace before publishing. |

---

## File Structure

```
home-assistant-orion-integration/
├── hacs.json                                    # HACS repo metadata
├── README.md                                    # User-facing docs (optional for now)
├── custom_components/
│   └── orion_sleep/                             # Domain name
│       ├── __init__.py                          # async_setup_entry / async_unload_entry
│       ├── manifest.json                        # HA integration manifest
│       ├── config_flow.py                       # Two-step auth + options flow
│       ├── const.py                             # DOMAIN, config keys, defaults
│       ├── coordinator.py                       # DataUpdateCoordinator
│       ├── api.py                               # Async API client (aiohttp)
│       ├── climate.py                           # Climate entities (per-zone temp control)
│       ├── sensor.py                            # Sleep insight sensors
│       ├── binary_sensor.py                     # Session active sensor
│       ├── switch.py                            # Sleep schedule enable/disable
│       ├── entity.py                            # Base entity with shared DeviceInfo
│       ├── strings.json                         # UI translations
│       └── diagnostics.py                       # Diagnostics support
```

---

## Implementation Order

### 1. `const.py` — Constants

```python
DOMAIN = "orion_sleep"
API_BASE_URL = "https://api1.orionbed.com"

# Config entry data keys (stored in config_entry.data)
CONF_AUTH_METHOD = "auth_method"       # "email" or "phone"
CONF_AUTH_VALUE = "auth_value"         # the email address or phone number
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_EXPIRES_AT = "expires_at"         # Unix timestamp

# Options flow keys
CONF_SCAN_INTERVAL = "scan_interval"   # polling interval in seconds
DEFAULT_SCAN_INTERVAL = 600            # 10 minutes

# Insights
CONF_INSIGHTS_DAYS = "insights_days"
DEFAULT_INSIGHTS_DAYS = 7
```

### 2. `api.py` — Async API Client

Port the logic from `orion_info.py` to async `aiohttp`. Key design:

```python
class OrionApiClient:
    """Async API client for Orion Sleep."""

    def __init__(self, session: aiohttp.ClientSession, access_token, refresh_token, expires_at):
        self._session = session
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._expires_at = expires_at
        self._token_refresh_callback = None  # called when tokens are refreshed

    # -- Auth methods (used by config_flow, no bearer token needed) --
    async def request_auth_code(self, email=None, phone=None) -> bool
    async def verify_auth_code(self, code, email=None, phone=None) -> dict
        # Returns {"access_token": ..., "refresh_token": ..., "expires_at": ...}
        # MUST extract from response.session (nested structure)

    # -- Token management --
    async def ensure_valid_token(self) -> None
        # Check expires_at with 60s margin, refresh if needed
        # On refresh, call self._token_refresh_callback(new_access, new_refresh, new_expires_at)
    async def _refresh_tokens(self) -> dict
        # POST /v1/auth/refresh with {"refresh_token": self._refresh_token}
        # Handle both nested (response.session) and top-level response shapes

    # -- Data fetchers (all require valid token) --
    async def get_current_user(self) -> dict
    async def list_devices(self) -> list[dict]
    async def get_sleep_config_devices(self) -> list[dict]
    async def get_session_state(self) -> dict
    async def get_sleep_schedules(self) -> dict
    async def get_insights(self, days=7) -> dict

    # -- Actions --
    async def set_temperature(self, device_id, temperature, side=None) -> dict
    async def set_user_away(self, device_id, side=None) -> None
    async def update_sleep_schedule(self, schedule_data, action=None) -> dict
```

**Critical porting notes from orion_info.py:**
- Response extraction: `(data.get("response") or {}).get("session")` — the `or {}` handles `None` response values
- Refresh body key is `refresh_token` (snake_case), NOT `refreshToken`
- Refresh response may be nested OR top-level: `(data.get("response") or {}).get("session", data)`
- Token expiry uses `expires_at` unix timestamp with 60-second margin, NOT JWT parsing
- All endpoints use `Content-Type: application/json` header
- Bearer token goes in `Authorization` header

**Error classes:**
```python
class OrionApiError(Exception): ...       # General API error
class OrionAuthError(OrionApiError): ...  # 401/auth failure
class OrionConnectionError(OrionApiError): ...  # Network error
```

### 3. `manifest.json`

```json
{
  "domain": "orion_sleep",
  "name": "Orion Sleep",
  "version": "1.0.0",
  "documentation": "https://github.com/YOUR_USERNAME/home-assistant-orion-integration",
  "issue_tracker": "https://github.com/YOUR_USERNAME/home-assistant-orion-integration/issues",
  "codeowners": ["@YOUR_USERNAME"],
  "config_flow": true,
  "dependencies": [],
  "requirements": [],
  "integration_type": "hub",
  "iot_class": "cloud_polling"
}
```

No external `requirements` needed — `aiohttp` is already bundled with HA.

### 4. `coordinator.py` — DataUpdateCoordinator

```python
class OrionDataUpdateCoordinator(DataUpdateCoordinator[dict]):
    """Fetch data from Orion API."""

    def __init__(self, hass, config_entry, api_client):
        interval = config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass, _LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=timedelta(seconds=interval),
        )
        self.api_client = api_client
        self.devices: list[dict] = []
        self.user: dict = {}

    async def _async_setup(self):
        """Load one-time data: user profile, device list."""
        self.user = await self.api_client.get_current_user()
        self.devices = await self.api_client.list_devices()

    async def _async_update_data(self) -> dict:
        """Poll mutable state."""
        await self.api_client.ensure_valid_token()

        sleep_configs = await self.api_client.get_sleep_config_devices()
        session_state = await self.api_client.get_session_state()
        schedules = await self.api_client.get_sleep_schedules()

        # Insights: fetch once per coordinator cycle (already rate-limited by polling interval)
        insights = await self.api_client.get_insights(
            days=self.config_entry.options.get(CONF_INSIGHTS_DAYS, DEFAULT_INSIGHTS_DAYS)
        )

        return {
            "sleep_configs": sleep_configs,
            "session_state": session_state,
            "schedules": schedules,
            "insights": insights,
        }
```

On `OrionAuthError` -> raise `ConfigEntryAuthFailed`.
On `OrionApiError` / `OrionConnectionError` -> raise `UpdateFailed`.

The token refresh callback updates `config_entry.data` so tokens persist across HA restarts:
```python
# In __init__.py or coordinator setup:
def _on_token_refresh(access_token, refresh_token, expires_at):
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data,
              CONF_ACCESS_TOKEN: access_token,
              CONF_REFRESH_TOKEN: refresh_token,
              CONF_EXPIRES_AT: expires_at},
    )
api_client._token_refresh_callback = _on_token_refresh
```

### 5. `config_flow.py` — Two-Step Auth + Options Flow

**Config flow steps:**
1. `async_step_user` — User enters auth method (email/phone) and value. Calls `POST /v1/auth/code`.
2. `async_step_verify` — User enters the 6-digit verification code. Calls `POST /v1/auth/verify`. Extracts tokens from nested `response.session`. Creates config entry.

**Reauth flow steps:**
1. `async_step_reauth` — Triggered by `ConfigEntryAuthFailed`. Shows confirmation form.
2. `async_step_reauth_confirm` — Sends new code, then goes to `async_step_verify`.

**Options flow:**
- `async_step_init` — User configures `scan_interval` (seconds) and `insights_days`.
- When options change, reload the integration to pick up new polling interval.

**Unique ID:** Use normalized `email.lower()` or phone number. Prevents duplicate entries.

### 6. `strings.json`

Must include translations for:
- Config flow: `user` step (auth method selector + value input), `verify` step (code input)
- Reauth: `reauth_confirm` step
- Options flow: `init` step (scan interval, insights days)
- Errors: `cannot_connect`, `invalid_code`, `invalid_auth`, `unknown`
- Abort reasons: `already_configured`, `reauth_successful`

### 7. `entity.py` — Base Entity

```python
class OrionBaseEntity(CoordinatorEntity[OrionDataUpdateCoordinator]):
    """Base entity for Orion Sleep."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator)
        self._device_id = device_id

    @property
    def device_info(self) -> DeviceInfo:
        device = self._get_device()
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=device.get("name", "Orion Sleep"),
            manufacturer="Orion Longevity",
            model="Orion Sleep",
            sw_version=device.get("firmwareVersion"),
        )

    def _get_device(self) -> dict:
        for d in self.coordinator.devices:
            if d.get("deviceId") == self._device_id or d.get("id") == self._device_id:
                return d
        return {}

    def _get_sleep_config(self) -> dict | None:
        for cfg in (self.coordinator.data or {}).get("sleep_configs", []):
            if cfg.get("deviceId") == self._device_id:
                return cfg
        return None
```

### 8. `climate.py` — Climate Entity

**One entity per zone/side.** If `splitZones` is true on a device's sleep config,
create two entities (left, right). If false, create one entity (no side specified).

```python
class OrionClimateEntity(OrionBaseEntity, ClimateEntity):
    _attr_hvac_modes = [HVACMode.HEAT_COOL, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_preset_modes = [PRESET_NONE, PRESET_AWAY]

    def __init__(self, coordinator, device_id, side=None):
        super().__init__(coordinator, device_id)
        self._side = side  # "left", "right", or None
        self._attr_unique_id = f"{device_id}_{side}" if side else device_id
        self._attr_translation_key = "bed_climate"

    @property
    def current_temperature(self):
        config = self._get_sleep_config()
        if not config:
            return None
        temp = config.get("temperature", {})
        return temp.get("current")

    @property
    def target_temperature(self):
        # If split zones, get from the user zone matching self._side
        # If not split, get from the top-level temperature config
        ...

    @property
    def target_temperature_step(self):
        config = self._get_sleep_config()
        increment = (config or {}).get("temperature", {}).get("controlIncrement", {})
        return increment.get("fahrenheit", 1.0)

    @property
    def hvac_mode(self):
        # Determine from session state or user zone data
        # If session is active or device is "on" -> HEAT_COOL
        # If device is off / user is away -> OFF
        ...

    async def async_set_temperature(self, **kwargs):
        temp = kwargs.get(ATTR_TEMPERATURE)
        await self.coordinator.api_client.set_temperature(
            device_id=self._device_id,
            temperature=temp,
            side=self._side,
        )
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode):
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.api_client.set_user_away(self._device_id, self._side)
        # HEAT_COOL: may need to "un-away" or start session — depends on API
        await self.coordinator.async_request_refresh()
```

**Entity discovery in `async_setup_entry`:**
```python
async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    entities = []
    for cfg in coordinator.data.get("sleep_configs", []):
        device_id = cfg["deviceId"]
        if cfg.get("splitZones"):
            entities.append(OrionClimateEntity(coordinator, device_id, side="left"))
            entities.append(OrionClimateEntity(coordinator, device_id, side="right"))
        else:
            entities.append(OrionClimateEntity(coordinator, device_id))
    async_add_entities(entities)
```

### 9. `sensor.py` — Sleep Insight Sensors

Create sensors from the latest day's insights data (`/v2/insights`).

| Sensor | Key Path | Unit | Device Class | State Class |
|---|---|---|---|---|
| Sleep Score | `dailySleepInsights[-1].sleepScore` | points | — | measurement |
| HRV | `dailySleepInsights[-1].hrv.value` | ms | — | measurement |
| Breath Rate | `dailySleepInsights[-1].breathRate.value` | breaths/min | — | measurement |
| Body Movement Rate | `dailySleepInsights[-1].bodyMovement.rate` | — | — | measurement |
| Restless Time | `dailySleepInsights[-1].bodyMovement.restlessTime` | min | duration | measurement |
| Times Left Bed | `dailySleepInsights[-1].bodyMovement.leftBed` | times | — | measurement |
| Total Sleep Time | `dailySleepInsights[-1].totalSleepTime` | min | duration | measurement |
| Awake Time | `dailySleepInsights[-1].sleepStages.awake` | min | duration | measurement |
| Light Sleep Time | `dailySleepInsights[-1].sleepStages.light` | min | duration | measurement |
| Deep Sleep Time | `dailySleepInsights[-1].sleepStages.deep` | min | duration | measurement |
| REM Sleep Time | `dailySleepInsights[-1].sleepStages.rem` | min | duration | measurement |

Use an `EntityDescription` pattern with a list of `SensorEntityDescription` for clean registration.

**Note:** Insights are per-user but associated with a device. Each sensor entity
should be tied to the device via `DeviceInfo`.

### 10. `binary_sensor.py` — Session Active

```python
class OrionSessionActiveBinarySensor(OrionBaseEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_translation_key = "sleep_session_active"

    @property
    def is_on(self):
        session = (self.coordinator.data or {}).get("session_state", {})
        return session.get("active", False)
```

### 11. `switch.py` — Sleep Schedule Enable/Disable

```python
class OrionScheduleSwitch(OrionBaseEntity, SwitchEntity):
    _attr_translation_key = "sleep_schedule"

    @property
    def is_on(self):
        schedules = (self.coordinator.data or {}).get("schedules", {})
        return schedules.get("enabled", False)

    async def async_turn_on(self, **kwargs):
        await self.coordinator.api_client.update_sleep_schedule(
            {"enabled": True}, action="enable"
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        await self.coordinator.api_client.update_sleep_schedule(
            {"enabled": False}, action="disable"
        )
        await self.coordinator.async_request_refresh()
```

### 12. `__init__.py` — Integration Setup

```python
PLATFORMS = [Platform.CLIMATE, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH]

async def async_setup_entry(hass, entry):
    session = async_get_clientsession(hass)
    api_client = OrionApiClient(
        session=session,
        access_token=entry.data[CONF_ACCESS_TOKEN],
        refresh_token=entry.data[CONF_REFRESH_TOKEN],
        expires_at=entry.data[CONF_EXPIRES_AT],
    )

    # Register token refresh callback to persist new tokens
    def on_token_refresh(access_token, refresh_token, expires_at):
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data,
                  CONF_ACCESS_TOKEN: access_token,
                  CONF_REFRESH_TOKEN: refresh_token,
                  CONF_EXPIRES_AT: expires_at},
        )
    api_client.set_token_refresh_callback(on_token_refresh)

    coordinator = OrionDataUpdateCoordinator(hass, entry, api_client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload on options change
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True

async def _async_options_updated(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
```

### 13. `diagnostics.py`

Redact sensitive fields (tokens, email, phone) and dump coordinator data for debugging.

### 14. `hacs.json` (repo root)

```json
{
  "name": "Orion Sleep",
  "homeassistant": "2024.1.0"
}
```

---

## OpenAPI Spec Reference: Schema Details

### SleepConfiguration
```yaml
deviceId: string
users: UserZone[]
temperature:
  current: float      # current bed temperature
  target: float       # target temperature
  controlIncrement:
    fahrenheit: float  # step size for temp control
    celsius: float
splitZones: boolean    # independent zones per side?
```

### UserZone
```yaml
userId: string
name: string
side: "left" | "right"
isAway: boolean
isGuest: boolean
targetTemperature: float
```

### SleepSchedule
```yaml
enabled: boolean
days: ScheduleDay[]
```

### ScheduleDay
```yaml
dayOfWeek: "monday" | ... | "sunday"
enabled: boolean
bedtime: "HH:mm"
wakeTime: "HH:mm"
autoTurnOff: boolean
phase2OffsetMinutes: integer
```

### DailySleepInsight
```yaml
date: date
hrv: {value: float}
breathRate: {value: float}
bodyMovement: {rate: float, restlessTime: float, leftBed: integer}
sleepScore: float
totalSleepTime: float   # minutes
sleepStages: {awake: float, light: float, deep: float, rem: float}  # minutes
```

### Session State
```yaml
active: boolean
sessionId: string
startedAt: datetime
```

### Device
```yaml
id: string
deviceId: string      # hardware identifier
name: string
orientation: "left" | "right"
firmwareVersion: string
batteryLevel: float
```

### User
```yaml
id: string
email: string
name: string
firstName: string
lastName: string
phone: string
emailVerified: boolean
devices: string[]     # device IDs
```

---

## API Endpoints Quick Reference

### Auth (no bearer token required)
| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/v1/auth/code` | `{email}` or `{phone}` | `{success: true}` |
| POST | `/v1/auth/verify` | `{code, email/phone}` | `{response: {session: {access_token, refresh_token, expires_at}}}` |
| POST | `/v1/auth/refresh` | `{refresh_token}` | Same nested or top-level session |

### Data (bearer token required)
| Method | Path | Params | Returns |
|---|---|---|---|
| GET | `/v1/auth/me` | — | User object |
| GET | `/v1/devices` | — | Device[] |
| GET | `/v1/sleep-configurations/devices` | — | SleepConfiguration[] |
| GET | `/v1/session-state` | — | `{active, sessionId, startedAt}` |
| GET | `/v1/sleep-schedules` | `?action=` | SleepSchedule |
| GET | `/v2/insights` | `?from=&to=` (ISO dates) | InsightsResponse |

### Actions (bearer token required)
| Method | Path | Body | Returns |
|---|---|---|---|
| PUT | `/v1/sleep-configurations/temperature` | `{deviceId, temperature, side?}` | SleepConfiguration |
| POST | `/v1/sleep-configurations/user-away` | `{deviceId, side?}` | — |
| PUT | `/v1/sleep-schedules` | SleepScheduleUpdate + `?action=` | SleepSchedule |

---

## Testing Notes

- The integration can be tested by copying `custom_components/orion_sleep/` into a
  HA dev environment's `config/custom_components/` directory.
- For initial testing, use `orion_info.py` to verify API connectivity and get sample
  response shapes from the live API.
- Consider adding a `__main__.py` or test script that uses the async API client
  standalone for API response validation.

---

## Out of Scope (Future Enhancements)

- WebSocket support for real-time temperature data
- Sleep session start/end controls
- Firmware update notifications
- Zone splitting/merging controls
- Guest user management
- Device onboarding (BLE pairing)
- NFC temperature patch management
- Sleep advisor meeting scheduling
