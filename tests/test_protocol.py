"""Folds every documented protocol v1 server frame through the coordinator.

Run directly: ``python tests/test_protocol.py``. No Home Assistant install needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stubs

stubs.install()

from types import SimpleNamespace

from custom_components.babymonitarr.api import normalise_ws_url
from custom_components.babymonitarr.coordinator import BabyMonitarrCoordinator
from custom_components.babymonitarr.entity import web_url

results = stubs.Results("protocol")
check = results.check


# --- URL normalisation ---
check("bare host", normalise_ws_url("1.2.3.4:5000"), "ws://1.2.3.4:5000/ha/ws")
check("http", normalise_ws_url("http://bm.local"), "ws://bm.local/ha/ws")
check("https", normalise_ws_url("https://bm.local/"), "wss://bm.local/ha/ws")
check("wss explicit path", normalise_ws_url("wss://bm.local/ha/ws"), "wss://bm.local/ha/ws")
try:
    normalise_ws_url("ftp://x")
    results.failures.append("bad scheme: no raise")
except ValueError:
    pass

# The device registry rejects a ws(s) configuration_url outright, which takes
# every entity on the hub device down with it.
check("web url wss", web_url("wss://bm.local/ha/ws"), "https://bm.local/")
check("web url ws", web_url("ws://1.2.3.4:5000/ha/ws"), "http://1.2.3.4:5000/")
check("web url subpath", web_url("wss://bm.local/baby/ha/ws"), "https://bm.local/baby/")

# --- coordinator folds against the documented frames ---
hass = stubs.Hass()
entry = stubs.ConfigEntryStub()
added = []
c = BabyMonitarrCoordinator(hass, entry, SimpleNamespace(set_callbacks=lambda a, b: None))
c.async_add_new_rooms_callback(added.extend)

c.handle_message("hello", {"protocol": 1, "server_version": "1.4.2", "level_interval_ms": 1000,
                           "sound_clear_hold_seconds": 30, "features": ["monitoring"]}, None)
check("connected on hello", c.data.connected, True)
check("server_version", c.data.server_version, "1.4.2")

c.handle_message("rooms", {"rooms": [{"id": 1, "name": "Nursery", "icon": "baby",
                                      "audio_enabled": True, "video_enabled": True,
                                      "source_type": "rtsp"}]}, None)
check("room added", added, [1])
check("room name", c.data.rooms[1].name, "Nursery")

c.handle_message("global_settings", {"sound_threshold_db": -20.0, "threshold_pause_seconds": 30,
                                     "volume_adjustment_db": -15.0, "audio_filter_enabled": False}, None)
check("setting", c.data.settings["sound_threshold_db"], -20.0)

c.handle_message("active_room", {"room_id": 1, "name": "Nursery"}, None)
check("active room", c.data.active_room_name, "Nursery")
c.handle_message("active_room", {"room_id": None, "name": None}, None)
check("active room cleared", c.data.active_room_id, None)

c.handle_message("connected_viewers", {"count": 2}, None)
check("viewers", c.data.connected_viewers, 2)

c.handle_message("room_state", {"room_id": 1, "monitoring": True, "sound_detected": False,
                                "stream_online": True, "level_db": -41.2}, None)
check("monitoring", c.data.state(1).monitoring, True)
check("level", c.data.state(1).level_db, -41.2)

c.handle_message("room_state", {"room_id": 2, "monitoring": False, "sound_detected": False,
                                "stream_online": False, "level_db": None}, None)
check("null level", c.data.state(2).level_db, None)

c.handle_message("ready", None, "8")
c.handle_message("sound_level", {"levels": [{"room_id": 1, "level_db": -38.41}]}, None)
check("peak level", c.data.state(1).level_db, -38.41)

c.handle_message("sound_state", {"room_id": 1, "detected": True,
                                 "last_event_at": "2026-09-22T12:34:56.789Z"}, None)
check("sound held", c.data.state(1).sound_detected, True)

before = c.pushes
c.handle_message("sound_event", {"room_id": 1, "level_db": -12.3, "threshold_db": -20.0,
                                 "at": "2026-09-22T12:34:56.789Z"}, None)
check("event fired", len(hass.bus.events), 1)
check("event name", hass.bus.events[0][0], "babymonitarr_sound_detected")
check("event room name", hass.bus.events[0][1]["room_name"], "Nursery")
check("sound_event does not push state", c.pushes, before)

c.handle_message("stream_online", {"room_id": 1, "online": False}, None)
check("stream offline", c.data.state(1).stream_online, False)
c.handle_message("monitoring", {"room_id": 1, "enabled": False}, None)
check("monitoring off", c.data.state(1).monitoring, False)

# webrtc.* is still reserved and must be ignored; cast.* is handled as of T4b
before = c.pushes
c.handle_message("webrtc.offer", {"room_id": 1, "session_id": "s"}, None)
c.handle_message("brand_new_type", {"whatever": 1}, None)
check("unknown types ignored", c.pushes, before)

c.handle_message("error", {"code": "unknown_room", "message": "No room 7"}, "9")
c.handle_message("ack", {"command": "set_monitoring"}, "9")
c.handle_message("pong", None, "7")

c.handle_connection(False)
check("disconnected", c.data.connected, False)
check("rooms kept across drop", list(c.data.rooms), [1])

sys.exit(results.report())
