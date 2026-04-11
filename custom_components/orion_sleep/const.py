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

# The Orion app displays temperature as an offset from a midpoint of 27°C.
# The API uses absolute Celsius (min=10, max=45).
# Verified: app shows -3 when API returns 24°C (24 - 27 = -3).
TEMP_OFFSET_MIDPOINT = 27.0
