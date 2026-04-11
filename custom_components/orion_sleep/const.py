"""Constants for the Orion Sleep integration."""

DOMAIN = "orion_sleep"
API_BASE_URL = "https://api1.orionbed.com"

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
