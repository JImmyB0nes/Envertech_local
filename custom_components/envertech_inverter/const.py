"""Constants for the Envertech Local integration."""

DOMAIN = "envertech_inverter"
MANUFACTURER = "JimmyBones"
# Display name of the integration in the HA UI / HACS (manifest.json "name").
INTEGRATION_NAME = "Envertech Local"
# Prefix baked into entity/device unique_ids and the device display name.
# Kept stable ("Envertech API") so existing entities keep their IDs across updates.
DEVICE_NAME = "Envertech API"
DEFAULT_PORT = 14889

# Polling of the inverter stream (seconds). Configurable per config entry via
# the options flow; the stream is restarted with the new value on change.
CONF_POLL_INTERVAL = "poll_interval"
DEFAULT_POLL_INTERVAL = 5
POLL_INTERVAL_CHOICES = (5, 10, 30)

# Seconds the config flow waits for UDP discovery replies before showing the
# form (and the manual-entry fallback).
DISCOVERY_TIMEOUT = 5.0
