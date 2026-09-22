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
MSG_CAST_DEVICES: Final = "cast.devices"
MSG_CAST_STATE: Final = "cast.state"
MSG_CAST_START_RESULT: Final = "cast.start_result"
MSG_WEBRTC_ANSWER: Final = "webrtc.answer"
MSG_WEBRTC_CANDIDATE: Final = "webrtc.candidate"
MSG_WEBRTC_CLOSED: Final = "webrtc.closed"

# Client -> server commands.
CMD_PING: Final = "ping"
CMD_GET_STATE: Final = "get_state"
CMD_SET_MONITORING: Final = "set_monitoring"
CMD_SET_GLOBAL_SETTINGS: Final = "set_global_settings"
CMD_SET_ACTIVE_ROOM: Final = "set_active_room"
CMD_CAST_DISCOVERED: Final = "cast.discovered"
CMD_CAST_START: Final = "cast.start"
CMD_CAST_STOP: Final = "cast.stop"
CMD_CAST_STOP_DEVICE: Final = "cast.stop_device"
CMD_CAST_SET_TARGETS: Final = "cast.set_targets"
CMD_WEBRTC_OFFER: Final = "webrtc.offer"
CMD_WEBRTC_CANDIDATE: Final = "webrtc.candidate"
CMD_WEBRTC_STOP: Final = "webrtc.stop"

# hello.features entries that gate the two prefixed message spaces.
FEATURE_CAST: Final = "cast"
FEATURE_WEBRTC: Final = "webrtc"

# Error codes the WebRTC path can answer an offer with.
ERROR_WEBRTC_CODEC_MISMATCH: Final = "webrtc_codec_mismatch"
ERROR_WEBRTC_FAILED: Final = "webrtc_failed"

# Video and audio are separate peer connections, selected by kind. The camera
# entity only ever negotiates video; levels come from the sound_level message,
# which needs no peer connection at all.
WEBRTC_KIND_VIDEO: Final = "video"
WEBRTC_KIND_AUDIO: Final = "audio"

# Global settings field names, exactly as the protocol spells them.
SETTING_SOUND_THRESHOLD_DB: Final = "sound_threshold_db"
SETTING_THRESHOLD_PAUSE_SECONDS: Final = "threshold_pause_seconds"
SETTING_VOLUME_ADJUSTMENT_DB: Final = "volume_adjustment_db"
SETTING_AUDIO_FILTER_ENABLED: Final = "audio_filter_enabled"

# Reconnect backoff for the receive loop.
RECONNECT_INITIAL_DELAY: Final = 1.0
RECONNECT_MAX_DELAY: Final = 60.0

# How long a command that expects a reply waits for the frame echoing its id.
# A cast start reaches out to real receivers, so it is not instant.
REQUEST_TIMEOUT: Final = 30.0

# How long setup waits for the backend to finish pushing its snapshot.
SNAPSHOT_TIMEOUT: Final = 20.0

# Identifier of the single global (hub) device.
GLOBAL_DEVICE_ID: Final = "global"

# --- Services ---------------------------------------------------------------

SERVICE_CAST_ROOM: Final = "cast_room"
SERVICE_STOP_CAST: Final = "stop_cast"
SERVICE_SNAPSHOT: Final = "snapshot"

ATTR_ROOM: Final = "room"
ATTR_TARGETS: Final = "targets"

# Attributes on sensor.<room>_cast_targets.
ATTR_TARGET_IDS: Final = "target_ids"
ATTR_TARGET_NAMES: Final = "target_names"
ATTR_CASTING_TO: Final = "casting_to"

# --- Zeroconf cast proxy ----------------------------------------------------

# mDNS TXT keys the backend already parses.
TXT_ID: Final = "id"
TXT_FRIENDLY_NAME: Final = "fn"
TXT_MODEL: Final = "md"
TXT_CAPABILITIES: Final = "ca"

DEFAULT_CAST_PORT: Final = 8009

# Chromecasts announce in bursts, so coalesce a burst into one push.
CAST_PUSH_DEBOUNCE_SECONDS: Final = 2.0

# Device trigger type backed by EVENT_SOUND_DETECTED.
TRIGGER_SOUND_DETECTED: Final = "sound_detected"
