"""Constants for the Orion Sleep integration."""

DOMAIN = "orion_sleep"
API_BASE_URL = "https://api1.orionbed.com"

# Live device WebSocket. Full URL is built as
# f"{WS_BASE_URL}/device/{serial_number}?token={jwt}".
WS_BASE_URL = "wss://live.api1.orionbed.com"
WS_USER_AGENT = "okhttp/4.12.0"  # what the Android app sends; known-good

# How stale a WS connection is allowed to get before we treat it as dropped
# (the server pushes a live_device.update at least every ~2s).
WS_STALE_AFTER_SECONDS = 30.0
# Exponential reconnect backoff bounds.
WS_RECONNECT_MIN_DELAY = 1.0
WS_RECONNECT_MAX_DELAY = 60.0

# Config entry data keys (stored in config_entry.data)
CONF_AUTH_METHOD = "auth_method"  # "email" or "phone"
CONF_AUTH_VALUE = "auth_value"  # the email address or phone number
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_EXPIRES_AT = "expires_at"  # Unix timestamp

# Options flow keys
CONF_SCAN_INTERVAL = "scan_interval"  # polling interval in seconds
DEFAULT_SCAN_INTERVAL = 600  # 10 minutes

# Insights
CONF_INSIGHTS_DAYS = "insights_days"
DEFAULT_INSIGHTS_DAYS = 7

# The Orion app displays temperature as a relative offset (-10 to +10).
# The mapping between offset and absolute Celsius is NON-LINEAR and comes
# from the device's temperature_scale.relative[] lookup table.
# Fallback table used when the device data isn't available yet:
DEFAULT_RELATIVE_TEMP_TABLE: list[dict[str, float]] = [
    {"in": -10, "out": 10},
    {"in": -9, "out": 12},
    {"in": -8, "out": 14},
    {"in": -7, "out": 16},
    {"in": -6, "out": 17.5},
    {"in": -5, "out": 19},
    {"in": -4, "out": 20.5},
    {"in": -3, "out": 23},
    {"in": -2, "out": 24.5},
    {"in": -1, "out": 26},
    {"in": 0, "out": 27.5},
    {"in": 1, "out": 29},
    {"in": 2, "out": 30.5},
    {"in": 3, "out": 32},
    {"in": 4, "out": 33.5},
    {"in": 5, "out": 35},
    {"in": 6, "out": 37},
    {"in": 7, "out": 39},
    {"in": 8, "out": 41},
    {"in": 9, "out": 43},
    {"in": 10, "out": 45},
]
