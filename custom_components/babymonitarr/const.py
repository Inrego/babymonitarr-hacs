"""Constants for the BabyMonitarr integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "babymonitarr"

CONF_HOST: Final = "host"
CONF_API_KEY: Final = "api_key"

# Bus event fired when a room's sound threshold is exceeded.
EVENT_SOUND_DETECTED: Final = "babymonitarr_sound_detected"

# Cast receivers are discovered here and pushed to the backend, because the
# backend's own mDNS browser cannot reach the LAN from a bridge network.
CAST_SERVICE_TYPE: Final = "_googlecast._tcp.local."

# --- Wire protocol (docs/ha-websocket-protocol.md, version 1) ---------------

PROTOCOL_VERSION: Final = 1
WS_PATH: Final = "/ha/ws"

# Server -> client message types.
MSG_HELLO: Final = "hello"
MSG_ROOMS: Final = "rooms"
MSG_GLOBAL_SETTINGS: Final = "global_settings"
MSG_ACTIVE_ROOM: Final = "active_room"
MSG_CONNECTED_VIEWERS: Final = "connected_viewers"
MSG_ROOM_STATE: Final = "room_state"
MSG_READY: Final = "ready"
MSG_SOUND_LEVEL: Final = "sound_level"
MSG_SOUND_STATE: Final = "sound_state"
MSG_SOUND_EVENT: Final = "sound_event"
MSG_STREAM_ONLINE: Final = "stream_online"
MSG_MONITORING: Final = "monitoring"
MSG_ACK: Final = "ack"
MSG_ERROR: Final = "error"
MSG_PONG: Final = "pong"

# Client -> server commands.
CMD_PING: Final = "ping"
CMD_GET_STATE: Final = "get_state"
CMD_SET_MONITORING: Final = "set_monitoring"
CMD_SET_GLOBAL_SETTINGS: Final = "set_global_settings"
CMD_SET_ACTIVE_ROOM: Final = "set_active_room"

# Global settings field names, exactly as the protocol spells them.
SETTING_SOUND_THRESHOLD_DB: Final = "sound_threshold_db"
SETTING_THRESHOLD_PAUSE_SECONDS: Final = "threshold_pause_seconds"
SETTING_VOLUME_ADJUSTMENT_DB: Final = "volume_adjustment_db"
SETTING_AUDIO_FILTER_ENABLED: Final = "audio_filter_enabled"

# Reconnect backoff for the receive loop.
RECONNECT_INITIAL_DELAY: Final = 1.0
RECONNECT_MAX_DELAY: Final = 60.0

# Identifier of the single global (hub) device.
GLOBAL_DEVICE_ID: Final = "global"
