"""Push coordinator for BabyMonitarr.

Why a ``DataUpdateCoordinator`` with ``update_interval=None`` rather than a bare
dispatcher store: the protocol is a snapshot followed by deltas, so there is a single
authoritative in-memory state object that every entity reads from. The coordinator
gives us exactly that plus listener bookkeeping, ``last_update_success`` and the
availability plumbing of ``CoordinatorEntity`` for free, and by never setting an
update interval it never polls - it is driven purely by ``async_set_updated_data``
from the WebSocket receive loop. That is the idiomatic shape for ``local_push``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import BabyMonitarrClient
from .const import (
    DOMAIN,
    EVENT_SOUND_DETECTED,
    FEATURE_CAST,
    FEATURE_WEBRTC,
    MSG_ACK,
    MSG_ACTIVE_ROOM,
    MSG_CAST_DEVICES,
    MSG_CAST_START_RESULT,
    MSG_CAST_STATE,
    MSG_CONNECTED_VIEWERS,
    MSG_ERROR,
    MSG_GLOBAL_SETTINGS,
    MSG_HELLO,
    MSG_MONITORING,
    MSG_PONG,
    MSG_READY,
    MSG_ROOM_STATE,
    MSG_ROOMS,
    MSG_SOUND_EVENT,
    MSG_SOUND_LEVEL,
    MSG_SOUND_STATE,
    MSG_STREAM_ONLINE,
    MSG_WEBRTC_ANSWER,
    MSG_WEBRTC_CANDIDATE,
    MSG_WEBRTC_CLOSED,
    SNAPSHOT_TIMEOUT,
)

if TYPE_CHECKING:
    from .cast_proxy import BabyMonitarrCastProxy

_LOGGER = logging.getLogger(__name__)

NewRoomsCallback = Callable[[list[int]], None]
SnapshotCallback = Callable[[], None]
# (message type, data) for one (room, kind) peer.
WebRtcCallback = Callable[[str, dict[str, Any]], None]


@dataclass(slots=True)
class RoomInfo:
    """A room as the ``rooms`` message describes it."""

    room_id: int
    name: str
    icon: str | None = None
    audio_enabled: bool = True
    video_enabled: bool = True
    source_type: str | None = None


@dataclass(slots=True)
class RoomState:
    """Per-room live state, from ``room_state`` and the incremental messages."""

    monitoring: bool = False
    sound_detected: bool = False
    stream_online: bool = False
    level_db: float | None = None
    last_event_at: str | None = None


@dataclass(slots=True)
class CastDevice:
    """A Cast receiver as ``cast.devices`` describes it.

    Identity is the mDNS TXT ``id`` whichever path found the device, so a receiver
    the backend browsed itself and one this integration proxied collapse to one
    row - protocol section 8.2.
    """

    device_id: str
    name: str
    model: str | None = None
    host: str | None = None
    port: int | None = None
    origin: str | None = None
    manually_added: bool = False
    is_video_capable: bool = False
    is_group: bool = False
    is_online: bool = False
    last_seen_at: str | None = None
    casting_room_id: int | None = None
    last_error: str | None = None


@dataclass(slots=True)
class CastSession:
    """One live cast session."""

    device_id: str
    video: bool = False
    started_at: str | None = None


@dataclass(slots=True)
class CastRoomState:
    """Per-room cast state, from ``cast.state``.

    ``targets`` is the saved selection and ``sessions`` is what is actually
    running; they are independent.
    """

    casting: bool = False
    targets: list[str] = field(default_factory=list)
    sessions: list[CastSession] = field(default_factory=list)


@dataclass(slots=True)
class BabyMonitarrData:
    """Everything the entities read."""

    connected: bool = False
    server_version: str | None = None
    features: list[str] = field(default_factory=list)
    level_interval_ms: int | None = None
    sound_clear_hold_seconds: int | None = None
    rooms: dict[int, RoomInfo] = field(default_factory=dict)
    room_states: dict[int, RoomState] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    active_room_id: int | None = None
    active_room_name: str | None = None
    connected_viewers: int | None = None
    cast_devices: dict[str, CastDevice] = field(default_factory=dict)
    cast_states: dict[int, CastRoomState] = field(default_factory=dict)

    def state(self, room_id: int) -> RoomState:
        """Return the state for a room, creating an empty one if unseen."""
        return self.room_states.setdefault(room_id, RoomState())

    def cast_state(self, room_id: int) -> CastRoomState:
        """Return the cast state for a room, creating an empty one if unseen."""
        return self.cast_states.setdefault(room_id, CastRoomState())

    @property
    def cast_supported(self) -> bool:
        """Whether the connected backend advertises the cast feature."""
        return FEATURE_CAST in self.features

    @property
    def webrtc_supported(self) -> bool:
        """Whether the connected backend advertises the WebRTC feature."""
        return FEATURE_WEBRTC in self.features

    def device_name(self, device_id: str) -> str:
        """The friendly name of a receiver, falling back to its id."""
        device = self.cast_devices.get(device_id)
        return device.name if device is not None and device.name else device_id


class BabyMonitarrCoordinator(DataUpdateCoordinator[BabyMonitarrData]):
    """Holds the room list, per-room state and global settings."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: BabyMonitarrClient
    ) -> None:
        """Initialise the coordinator. No update interval: this is push-only."""
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=None)
        # Assigned rather than passed as a kwarg: the `config_entry` argument only
        # exists on newer cores, and this integration targets 2024.11+.
        self.config_entry = entry
        self.client = client
        self.data = BabyMonitarrData()
        # Owned by __init__.py; declared here so the services and platforms can
        # reach it through the entry's runtime data.
        self.cast_proxy: BabyMonitarrCastProxy | None = None
        self._new_rooms_callbacks: list[NewRoomsCallback] = []
        self._snapshot_callbacks: list[SnapshotCallback] = []
        self._webrtc_callbacks: dict[tuple[int, str], WebRtcCallback] = {}
        self._snapshot_received = asyncio.Event()
        client.set_callbacks(self.handle_message, self.handle_connection)

    # --- entity plumbing ----------------------------------------------------

    @callback
    def async_add_new_rooms_callback(self, cb: NewRoomsCallback) -> None:
        """Register a platform's "rooms appeared" hook.

        Rooms arrive over the wire, so every platform adds its entities lazily
        rather than at setup time.
        """
        self._new_rooms_callbacks.append(cb)

    @callback
    def _async_announce_rooms(self, room_ids: list[int]) -> None:
        for cb in self._new_rooms_callbacks:
            cb(room_ids)

    @callback
    def async_add_snapshot_callback(self, cb: SnapshotCallback) -> CALLBACK_TYPE:
        """Register a hook fired whenever the backend starts a fresh snapshot.

        The cast proxy uses it to re-push ``cast.discovered`` on reconnect, which
        protocol section 5 asks for: proxy-discovered devices are persisted with
        their last known host, but only HA can refresh them.
        """
        self._snapshot_callbacks.append(cb)

        @callback
        def _remove() -> None:
            self._snapshot_callbacks.remove(cb)

        return _remove

    async def async_wait_for_snapshot(self, timeout: float = SNAPSHOT_TIMEOUT) -> None:
        """Wait until the backend has finished pushing its snapshot.

        Setup needs this before it can read ``hello.features`` or hand the room
        list to the platforms. Raises :class:`TimeoutError` if it never arrives.
        """
        async with asyncio.timeout(timeout):
            await self._snapshot_received.wait()

    @callback
    def async_register_webrtc(
        self, room_id: int, kind: str, cb: WebRtcCallback
    ) -> CALLBACK_TYPE:
        """Route this peer's unsolicited ``webrtc.*`` frames to ``cb``.

        The backend keeps one peer per (connection, room, kind), so that tuple is
        the whole routing key. ``webrtc.answer`` is not routed here - it carries a
        ``ref`` and is returned to whoever sent the offer.
        """
        key = (room_id, kind)
        self._webrtc_callbacks[key] = cb

        @callback
        def _remove() -> None:
            if self._webrtc_callbacks.get(key) is cb:
                del self._webrtc_callbacks[key]

        return _remove

    # --- transport callbacks ------------------------------------------------

    @callback
    def handle_connection(self, connected: bool) -> None:
        """React to the socket coming up or going away."""
        self.data.connected = connected
        if not connected:
            # Everything derived is unknown until the next snapshot; the backend
            # re-sends the full snapshot on connect, so we do not clear the room
            # list (that would churn entities on every blip).
            self._snapshot_received.clear()
            self._async_fail_webrtc_peers("The connection to BabyMonitarr dropped")
            _LOGGER.debug("BabyMonitarr disconnected")
        self.async_set_updated_data(self.data)

    @callback
    def handle_message(
        self, msg_type: str, data: dict[str, Any] | None, ref: str | None
    ) -> None:
        """Fold one server frame into the state and push it to the entities.

        Unknown message types are ignored on purpose - that is what lets the
        backend add ``webrtc.*`` and ``cast.*`` without a version bump.
        """
        handler = _HANDLERS.get(msg_type)
        if handler is None:
            _LOGGER.debug("Ignoring unhandled message type %s", msg_type)
            return
        if handler(self, data or {}):
            self.async_set_updated_data(self.data)

    # --- per-message folds --------------------------------------------------
    # Each returns True when entities should be told.

    def _on_hello(self, data: dict[str, Any]) -> bool:
        self.data.connected = True
        self.data.server_version = data.get("server_version")
        features = data.get("features")
        self.data.features = list(features) if isinstance(features, list) else []
        self.data.level_interval_ms = data.get("level_interval_ms")
        self.data.sound_clear_hold_seconds = data.get("sound_clear_hold_seconds")
        return True

    def _on_rooms(self, data: dict[str, Any]) -> bool:
        raw = data.get("rooms")
        if not isinstance(raw, list):
            return False
        rooms: dict[int, RoomInfo] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            room_id = item.get("id")
            if not isinstance(room_id, int):
                continue
            rooms[room_id] = RoomInfo(
                room_id=room_id,
                name=str(item.get("name") or f"Room {room_id}"),
                icon=item.get("icon"),
                audio_enabled=bool(item.get("audio_enabled", True)),
                video_enabled=bool(item.get("video_enabled", True)),
                source_type=item.get("source_type"),
            )
        new_ids = [room_id for room_id in rooms if room_id not in self.data.rooms]
        self.data.rooms = rooms
        for room_id in rooms:
            self.data.state(room_id)
        if new_ids:
            self._async_announce_rooms(new_ids)
        return True

    def _on_global_settings(self, data: dict[str, Any]) -> bool:
        self.data.settings = dict(data)
        return True

    def _on_active_room(self, data: dict[str, Any]) -> bool:
        room_id = data.get("room_id")
        self.data.active_room_id = room_id if isinstance(room_id, int) else None
        name = data.get("name")
        self.data.active_room_name = name if isinstance(name, str) else None
        return True

    def _on_connected_viewers(self, data: dict[str, Any]) -> bool:
        count = data.get("count")
        self.data.connected_viewers = count if isinstance(count, int) else None
        return True

    def _on_room_state(self, data: dict[str, Any]) -> bool:
        room_id = data.get("room_id")
        if not isinstance(room_id, int):
            return False
        state = self.data.state(room_id)
        state.monitoring = bool(data.get("monitoring", False))
        state.sound_detected = bool(data.get("sound_detected", False))
        state.stream_online = bool(data.get("stream_online", False))
        level = data.get("level_db")
        state.level_db = float(level) if isinstance(level, (int, float)) else None
        return True

    def _on_ready(self, data: dict[str, Any]) -> bool:
        _LOGGER.debug("BabyMonitarr snapshot complete: %d room(s)", len(self.data.rooms))
        self._snapshot_received.set()
        for cb in list(self._snapshot_callbacks):
            cb()
        return True

    def _on_cast_devices(self, data: dict[str, Any]) -> bool:
        raw = data.get("devices")
        if not isinstance(raw, list):
            return False
        devices: dict[str, CastDevice] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            device_id = item.get("device_id")
            if not isinstance(device_id, str) or not device_id:
                continue
            port = item.get("port")
            casting_room_id = item.get("casting_room_id")
            devices[device_id] = CastDevice(
                device_id=device_id,
                name=str(item.get("name") or device_id),
                model=item.get("model") or None,
                host=item.get("host") or None,
                port=port if isinstance(port, int) else None,
                origin=item.get("origin") or None,
                manually_added=bool(item.get("manually_added", False)),
                is_video_capable=bool(item.get("is_video_capable", False)),
                is_group=bool(item.get("is_group", False)),
                is_online=bool(item.get("is_online", False)),
                last_seen_at=item.get("last_seen_at") or None,
                casting_room_id=(
                    casting_room_id if isinstance(casting_room_id, int) else None
                ),
                last_error=item.get("last_error") or None,
            )
        self.data.cast_devices = devices
        return True

    def _on_cast_state(self, data: dict[str, Any]) -> bool:
        room_id = data.get("room_id")
        if not isinstance(room_id, int):
            return False
        state = self.data.cast_state(room_id)
        state.casting = bool(data.get("casting", False))
        targets = data.get("targets")
        state.targets = (
            [t for t in targets if isinstance(t, str)]
            if isinstance(targets, list)
            else []
        )
        sessions = data.get("sessions")
        state.sessions = []
        if isinstance(sessions, list):
            for item in sessions:
                if not isinstance(item, dict):
                    continue
                device_id = item.get("device_id")
                if not isinstance(device_id, str):
                    continue
                state.sessions.append(
                    CastSession(
                        device_id=device_id,
                        video=bool(item.get("video", False)),
                        started_at=item.get("started_at") or None,
                    )
                )
        return True

    def _on_sound_level(self, data: dict[str, Any]) -> bool:
        levels = data.get("levels")
        if not isinstance(levels, list):
            return False
        changed = False
        for item in levels:
            if not isinstance(item, dict):
                continue
            room_id = item.get("room_id")
            level = item.get("level_db")
            if not isinstance(room_id, int) or not isinstance(level, (int, float)):
                continue
            self.data.state(room_id).level_db = float(level)
            changed = True
        return changed

    def _on_sound_state(self, data: dict[str, Any]) -> bool:
        room_id = data.get("room_id")
        if not isinstance(room_id, int):
            return False
        state = self.data.state(room_id)
        # The 30 s clear-hold is applied server-side; this is already the held
        # state, so it drives the binary sensor verbatim.
        state.sound_detected = bool(data.get("detected", False))
        last_event_at = data.get("last_event_at")
        state.last_event_at = last_event_at if isinstance(last_event_at, str) else None
        return True

    def _on_sound_event(self, data: dict[str, Any]) -> bool:
        room_id = data.get("room_id")
        if not isinstance(room_id, int):
            return False
        room = self.data.rooms.get(room_id)
        self.hass.bus.async_fire(
            EVENT_SOUND_DETECTED,
            {
                "entry_id": self.config_entry.entry_id,
                "room_id": room_id,
                "room_name": room.name if room else None,
                "level_db": data.get("level_db"),
                "threshold_db": data.get("threshold_db"),
                "at": data.get("at"),
            },
        )
        # The discrete crossing does not move any entity state; sound_state does.
        return False

    def _on_stream_online(self, data: dict[str, Any]) -> bool:
        room_id = data.get("room_id")
        if not isinstance(room_id, int):
            return False
        self.data.state(room_id).stream_online = bool(data.get("online", False))
        return True

    def _on_monitoring(self, data: dict[str, Any]) -> bool:
        room_id = data.get("room_id")
        if not isinstance(room_id, int):
            return False
        self.data.state(room_id).monitoring = bool(data.get("enabled", False))
        return True

    def _on_webrtc(self, data: dict[str, Any], msg_type: str) -> bool:
        """Hand one unsolicited ``webrtc.*`` frame to the peer that owns it."""
        room_id = data.get("room_id")
        kind = data.get("kind")
        if not isinstance(room_id, int) or not isinstance(kind, str):
            return False
        cb = self._webrtc_callbacks.get((room_id, kind))
        if cb is None:
            # A candidate for a peer we already tore down; dropping it is correct.
            _LOGGER.debug("No WebRTC peer for room %s %s; dropping %s", room_id, kind, msg_type)
            return False
        cb(msg_type, data)
        # WebRTC signalling moves no entity state.
        return False

    def _on_webrtc_candidate(self, data: dict[str, Any]) -> bool:
        return self._on_webrtc(data, MSG_WEBRTC_CANDIDATE)

    def _on_webrtc_closed(self, data: dict[str, Any]) -> bool:
        return self._on_webrtc(data, MSG_WEBRTC_CLOSED)

    @callback
    def _async_fail_webrtc_peers(self, reason: str) -> None:
        """Tell every peer it is gone.

        Protocol section 5: a dropped socket closes every peer it owned server-side
        and no ``webrtc.closed`` arrives, because the socket carrying it is already
        gone. Without this the frontend would sit on a frozen stream.
        """
        for (room_id, kind), cb in list(self._webrtc_callbacks.items()):
            cb(MSG_WEBRTC_CLOSED, {"room_id": room_id, "kind": kind, "reason": reason})

    def _on_error(self, data: dict[str, Any]) -> bool:
        _LOGGER.warning(
            "BabyMonitarr error %s: %s", data.get("code"), data.get("message")
        )
        return False

    def _ignore(self, data: dict[str, Any]) -> bool:
        return False

    # --- commands used by entities -----------------------------------------

    async def async_set_monitoring(self, room_id: int, enabled: bool) -> None:
        """Ask the backend to change a room's always-on subscription."""
        await self.client.async_set_monitoring(room_id, enabled)

    async def async_set_global_setting(self, field_name: str, value: Any) -> None:
        """Write one global setting. The backend echoes a ``global_settings``."""
        await self.client.async_set_global_settings(**{field_name: value})

    # --- cast commands used by the services --------------------------------

    async def async_cast_start(
        self, room_id: int, device_ids: list[str] | None
    ) -> dict[str, Any]:
        """Start casting a room and return the ``cast.start_result`` payload.

        A partial failure is a success at the protocol level, so the caller gets
        both ``started`` and ``failed`` and decides what to say about them.
        """
        msg_type, data = await self.client.async_cast_start(room_id, device_ids)
        _raise_on_error(msg_type, data)
        if msg_type != MSG_CAST_START_RESULT:
            raise HomeAssistantError(
                f"Unexpected reply to cast.start: {msg_type}"
            )
        return data or {}

    async def async_cast_stop(self, room_id: int) -> None:
        """Stop every cast session for a room."""
        msg_type, data = await self.client.async_cast_stop(room_id)
        _raise_on_error(msg_type, data)

    async def async_cast_set_targets(
        self, room_id: int, device_ids: list[str]
    ) -> None:
        """Replace a room's saved target selection."""
        msg_type, data = await self.client.async_cast_set_targets(room_id, device_ids)
        _raise_on_error(msg_type, data)

    async def async_cast_discovered(self, devices: list[dict[str, Any]]) -> None:
        """Push the mDNS proxy's current set of receivers."""
        await self.client.async_cast_discovered(devices)

    # --- lookups the services need ------------------------------------------

    def resolve_room(self, room: str | int) -> int:
        """Turn a room name or id from a service call into a room id."""
        if isinstance(room, int) or (isinstance(room, str) and room.isdigit()):
            room_id = int(room)
            if room_id in self.data.rooms:
                return room_id
            raise ServiceValidationError(f"No BabyMonitarr room with id {room_id}")
        wanted = str(room).casefold()
        for info in self.data.rooms.values():
            if info.name.casefold() == wanted:
                return info.room_id
        known = ", ".join(sorted(info.name for info in self.data.rooms.values()))
        raise ServiceValidationError(
            f"No BabyMonitarr room named {room!r}. Known rooms: {known or 'none'}"
        )

    def resolve_targets(self, targets: list[str]) -> list[str]:
        """Turn target names or ids into device ids, rejecting unknown ones."""
        resolved: list[str] = []
        by_name = {
            device.name.casefold(): device.device_id
            for device in self.data.cast_devices.values()
        }
        for target in targets:
            if target in self.data.cast_devices:
                resolved.append(target)
                continue
            device_id = by_name.get(target.casefold())
            if device_id is None:
                known = ", ".join(
                    sorted(d.name for d in self.data.cast_devices.values())
                )
                raise ServiceValidationError(
                    f"Unknown Cast target {target!r}. Known devices: {known or 'none'}"
                )
            resolved.append(device_id)
        return resolved


def _raise_on_error(msg_type: str, data: dict[str, Any] | None) -> None:
    """Turn an ``error`` reply into a Home Assistant error."""
    if msg_type != MSG_ERROR:
        return
    payload = data or {}
    raise HomeAssistantError(
        f"BabyMonitarr rejected the command ({payload.get('code', 'error')}): "
        f"{payload.get('message', 'no detail')}"
    )


_HANDLERS: dict[str, Callable[[BabyMonitarrCoordinator, dict[str, Any]], bool]] = {
    MSG_HELLO: BabyMonitarrCoordinator._on_hello,
    MSG_ROOMS: BabyMonitarrCoordinator._on_rooms,
    MSG_GLOBAL_SETTINGS: BabyMonitarrCoordinator._on_global_settings,
    MSG_ACTIVE_ROOM: BabyMonitarrCoordinator._on_active_room,
    MSG_CONNECTED_VIEWERS: BabyMonitarrCoordinator._on_connected_viewers,
    MSG_ROOM_STATE: BabyMonitarrCoordinator._on_room_state,
    MSG_READY: BabyMonitarrCoordinator._on_ready,
    MSG_SOUND_LEVEL: BabyMonitarrCoordinator._on_sound_level,
    MSG_SOUND_STATE: BabyMonitarrCoordinator._on_sound_state,
    MSG_SOUND_EVENT: BabyMonitarrCoordinator._on_sound_event,
    MSG_STREAM_ONLINE: BabyMonitarrCoordinator._on_stream_online,
    MSG_CAST_DEVICES: BabyMonitarrCoordinator._on_cast_devices,
    MSG_CAST_STATE: BabyMonitarrCoordinator._on_cast_state,
    MSG_WEBRTC_CANDIDATE: BabyMonitarrCoordinator._on_webrtc_candidate,
    MSG_WEBRTC_CLOSED: BabyMonitarrCoordinator._on_webrtc_closed,
    # Carries a ref, so async_request has already returned it to the offerer.
    MSG_WEBRTC_ANSWER: BabyMonitarrCoordinator._ignore,
    MSG_MONITORING: BabyMonitarrCoordinator._on_monitoring,
    MSG_ERROR: BabyMonitarrCoordinator._on_error,
    MSG_ACK: BabyMonitarrCoordinator._ignore,
    MSG_PONG: BabyMonitarrCoordinator._ignore,
}
