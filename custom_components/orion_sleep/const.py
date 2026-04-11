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

# Temperature display mode
CONF_TEMP_MODE = "temperature_mode"
TEMP_MODE_ABSOLUTE = "absolute"  # Show real Celsius (10-45°C)
TEMP_MODE_OFFSET = "offset"  # Show relative offset (-10 to +10) like the app
DEFAULT_TEMP_MODE = TEMP_MODE_OFFSET

# The Orion app displays temperature as an offset from a midpoint.
# The API uses absolute Celsius (min=10, max=45). The midpoint is 27.5°C.
TEMP_OFFSET_MIDPOINT = 27.5
