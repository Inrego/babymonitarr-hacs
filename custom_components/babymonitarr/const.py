"""Constants for the BabyMonitarr integration."""

DOMAIN = "babymonitarr"

CONF_API_KEY = "api_key"

# Bus event fired when a room's sound threshold is exceeded.
EVENT_SOUND_DETECTED = "babymonitarr_sound_detected"

# Cast receivers are discovered here and pushed to the backend, because the
# backend's own mDNS browser cannot reach the LAN from a bridge network.
CAST_SERVICE_TYPE = "_googlecast._tcp.local."
