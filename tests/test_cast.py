"""Cast: mDNS TXT extraction, the cast.* folds, target resolution and replies.

Run directly: ``python tests/test_cast.py``. No Home Assistant install needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stubs

stubs.install()

import asyncio
from types import SimpleNamespace

from custom_components.babymonitarr.api import (
    BabyMonitarrClient,
    BabyMonitarrConnectionError,
)
from custom_components.babymonitarr.cast_proxy import build_device
from custom_components.babymonitarr.coordinator import (
    BabyMonitarrCoordinator,
)

results = stubs.Results("cast")
check = results.check
expect_raises = results.expect_raises


# --- cast TXT extraction ----------------------------------------------------
class _FakeInfo:
    def __init__(self, properties, addresses, port=8009):
        self.properties = properties
        self._addresses = addresses
        self.port = port

    def parsed_scoped_addresses(self):
        return self._addresses


device = build_device(
    _FakeInfo(
        {
            b"id": b"1f2e3d4c5b6a",
            b"fn": b"Living Room TV",
            b"md": b"Chromecast Ultra",
            b"ca": b"4101",
            b"rs": b"",
        },
        ["192.168.1.42"],
    )
)
check("txt id", device["id"], "1f2e3d4c5b6a")
check("txt host", device["host"], "192.168.1.42")
check("txt port", device["port"], 8009)
check("txt fn", device["fn"], "Living Room TV")
check("txt md", device["md"], "Chromecast Ultra")
check("txt ca", device["ca"], "4101")
check("no empty txt key kept", "rs" in device, False)

check("no id -> skipped", build_device(_FakeInfo({b"fn": b"X"}, ["1.2.3.4"])), None)
check("no address -> skipped", build_device(_FakeInfo({b"id": b"abc"}, [])), None)
check(
    "default port",
    build_device(_FakeInfo({b"id": b"abc"}, ["1.2.3.4"], port=None))["port"],
    8009,
)

# --- coordinator cast folds -------------------------------------------------
hass = stubs.Hass()
entry = stubs.ConfigEntryStub()
client_stub = SimpleNamespace(set_callbacks=lambda a, b: None)
c = BabyMonitarrCoordinator(hass, entry, client_stub)

snapshots = []
unsub = c.async_add_snapshot_callback(lambda: snapshots.append(1))

c.handle_message("hello", {"server_version": "1.5.0", "features": ["rooms", "cast"]}, None)
check("cast feature detected", c.data.cast_supported, True)

c.handle_message("rooms", {"rooms": [{"id": 1, "name": "Nursery"},
                                     {"id": 2, "name": "Guest Room"}]}, None)

c.handle_message("cast.devices", {"devices": [
    {"device_id": "1f2e", "name": "Living Room TV", "model": "Chromecast Ultra",
     "host": "192.168.1.42", "port": 8009, "origin": "ha-proxy",
     "manually_added": False, "is_video_capable": True, "is_group": False,
     "is_online": True, "last_seen_at": "2026-09-22T12:00:00Z",
     "casting_room_id": 1, "last_error": None},
    {"device_id": "9a8b", "name": "Kitchen Speaker", "host": "192.168.1.50",
     "port": 8009, "origin": "discovered", "is_group": True, "is_online": False},
]}, None)
check("devices folded", sorted(c.data.cast_devices), ["1f2e", "9a8b"])
check("device model", c.data.cast_devices["1f2e"].model, "Chromecast Ultra")
check("device video capable", c.data.cast_devices["1f2e"].is_video_capable, True)
check("device group", c.data.cast_devices["9a8b"].is_group, True)
check("casting room id", c.data.cast_devices["1f2e"].casting_room_id, 1)
check("device name lookup", c.data.device_name("9a8b"), "Kitchen Speaker")
check("unknown device name falls back", c.data.device_name("zzz"), "zzz")

c.handle_message("cast.state", {"room_id": 1, "casting": True,
                                "targets": ["1f2e", "9a8b"],
                                "sessions": [{"device_id": "1f2e", "video": True,
                                              "started_at": "2026-09-22T12:00:00Z"}]}, None)
check("casting", c.data.cast_state(1).casting, True)
check("targets", c.data.cast_state(1).targets, ["1f2e", "9a8b"])
check("sessions", len(c.data.cast_state(1).sessions), 1)
check("session video", c.data.cast_state(1).sessions[0].video, True)

# saved targets with no live session is not casting
c.handle_message("cast.state", {"room_id": 2, "casting": False,
                                "targets": ["9a8b"], "sessions": []}, None)
check("room 2 not casting", c.data.cast_state(2).casting, False)
check("room 2 has a saved target", c.data.cast_state(2).targets, ["9a8b"])

# sessions replaced, not appended, on the next frame
c.handle_message("cast.state", {"room_id": 1, "casting": False, "targets": [],
                                "sessions": []}, None)
check("sessions replaced", c.data.cast_state(1).sessions, [])

check("snapshot callback not fired yet", snapshots, [])
c.handle_message("ready", None, None)
check("snapshot callback fired on ready", snapshots, [1])
unsub()
c.handle_message("ready", None, None)
check("snapshot callback unsubscribed", snapshots, [1])

# --- room and target resolution --------------------------------------------
check("room by name", c.resolve_room("Nursery"), 1)
check("room by name, case-insensitive", c.resolve_room("nursery"), 1)
check("room by id string", c.resolve_room("2"), 2)
check("room by id int", c.resolve_room(2), 2)
expect_raises("unknown room name", stubs.ServiceValidationError, c.resolve_room, "Attic")
expect_raises("unknown room id", stubs.ServiceValidationError, c.resolve_room, "77")

check("target by id", c.resolve_targets(["1f2e"]), ["1f2e"])
check("target by name", c.resolve_targets(["Living Room TV"]), ["1f2e"])
check("targets mixed", c.resolve_targets(["9a8b", "Living Room TV"]), ["9a8b", "1f2e"])
expect_raises("unknown target", stubs.ServiceValidationError, c.resolve_targets, ["Attic TV"])

# --- api request/reply correlation -----------------------------------------
class _FakeWS:
    closed = False

    def __init__(self):
        self.sent = []

    async def send_json(self, frame):
        self.sent.append(frame)


async def _test_request():
    client = BabyMonitarrClient(None, "ws://x/ha/ws", "key")
    ws = _FakeWS()
    client._ws = ws

    async def _reply_after_send(reply_type, reply_data):
        while not ws.sent:
            await asyncio.sleep(0)
        ref = ws.sent[-1]["id"]
        client._resolve_pending(ref, reply_type, reply_data)

    # cast.start -> cast.start_result
    task = asyncio.create_task(_reply_after_send(
        "cast.start_result",
        {"room_id": 1, "started": [{"device_id": "1f2e"}], "failed": {"9a8b": "nope"}},
    ))
    msg_type, data = await client.async_cast_start(1, ["1f2e", "9a8b"])
    await task
    check("start reply type", msg_type, "cast.start_result")
    check("start failed surfaced", data["failed"], {"9a8b": "nope"})
    check("start frame type", ws.sent[-1]["type"], "cast.start")
    check("start device_ids", ws.sent[-1]["data"]["device_ids"], ["1f2e", "9a8b"])

    # no device ids -> the field is omitted, so the backend uses saved targets
    task = asyncio.create_task(_reply_after_send("cast.start_result", {"started": []}))
    await client.async_cast_start(1, None)
    await task
    check("saved targets frame omits device_ids", "device_ids" in ws.sent[-1]["data"], False)

    # cast.stop -> ack
    task = asyncio.create_task(_reply_after_send("ack", {"command": "cast.stop"}))
    msg_type, _ = await client.async_cast_stop(1)
    await task
    check("stop reply type", msg_type, "ack")

    # cast.discovered is fire-and-forget and sends the whole set
    await client.async_cast_discovered([{"id": "1f2e", "host": "192.168.1.42"}])
    check("discovered frame type", ws.sent[-1]["type"], "cast.discovered")
    check("discovered devices", len(ws.sent[-1]["data"]["devices"]), 1)

    # a dropped socket fails everything in flight
    pending = asyncio.create_task(client.async_cast_stop(1))
    await asyncio.sleep(0)
    client._fail_pending(BabyMonitarrConnectionError("Connection lost"))
    try:
        await pending
        results.failures.append("dropped socket: no error raised")
    except BabyMonitarrConnectionError:
        pass


asyncio.run(_test_request())

# --- coordinator surfaces a protocol error as a HA error --------------------
async def _test_error_surface():
    calls = {}

    async def _fake_start(room_id, device_ids):
        calls["args"] = (room_id, device_ids)
        return ("error", {"code": "unknown_room", "message": "No room 7"})

    c.client = SimpleNamespace(async_cast_start=_fake_start)
    try:
        await c.async_cast_start(7, None)
        results.failures.append("error reply: nothing raised")
    except stubs.HomeAssistantError as err:
        if "unknown_room" not in str(err):
            results.failures.append(f"error reply: unhelpful message {err!r}")


asyncio.run(_test_error_surface())

sys.exit(results.report())
