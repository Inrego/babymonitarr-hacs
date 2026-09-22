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

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import BabyMonitarrClient
from .const import (
    DOMAIN,
    EVENT_SOUND_DETECTED,
    MSG_ACK,
    MSG_ACTIVE_ROOM,
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
)

_LOGGER = logging.getLogger(__name__)

NewRoomsCallback = Callable[[list[int]], None]


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

    def state(self, room_id: int) -> RoomState:
        """Return the state for a room, creating an empty one if unseen."""
        return self.room_states.setdefault(room_id, RoomState())


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
        self._new_rooms_callbacks: list[NewRoomsCallback] = []
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

    # --- transport callbacks ------------------------------------------------

    @callback
    def handle_connection(self, connected: bool) -> None:
        """React to the socket coming up or going away."""
        self.data.connected = connected
        if not connected:
            # Everything derived is unknown until the next snapshot; the backend
            # re-sends the full snapshot on connect, so we do not clear the room
            # list (that would churn entities on every blip).
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
    MSG_MONITORING: BabyMonitarrCoordinator._on_monitoring,
    MSG_ERROR: BabyMonitarrCoordinator._on_error,
    MSG_ACK: BabyMonitarrCoordinator._ignore,
    MSG_PONG: BabyMonitarrCoordinator._ignore,
}
