"""Shared entity bases and device wiring for BabyMonitarr.

Two device shapes, per docs/DESIGN.md: one device per room, plus one global device
that owns the settings and the hub-wide sensors. A future camera platform and the
cast entities hang off the same room device via :func:`room_device_info`.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, GLOBAL_DEVICE_ID
from .coordinator import (
    BabyMonitarrCoordinator,
    CastRoomState,
    RoomInfo,
    RoomState,
)


def global_device_info(entry_id: str, coordinator: BabyMonitarrCoordinator) -> DeviceInfo:
    """The single hub device."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_{GLOBAL_DEVICE_ID}")},
        name="BabyMonitarr",
        manufacturer="BabyMonitarr",
        model="Hub",
        sw_version=coordinator.data.server_version,
        configuration_url=coordinator.client.url,
    )


def room_device_info(entry_id: str, room: RoomInfo) -> DeviceInfo:
    """One device per room, parented to the hub device."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_room_{room.room_id}")},
        name=room.name,
        manufacturer="BabyMonitarr",
        model="Room",
        via_device=(DOMAIN, f"{entry_id}_{GLOBAL_DEVICE_ID}"),
    )


class BabyMonitarrEntity(CoordinatorEntity[BabyMonitarrCoordinator]):
    """Common base: availability follows the WebSocket connection."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BabyMonitarrCoordinator, key: str) -> None:
        """Initialise with the unique_id suffix ``key``."""
        super().__init__(coordinator)
        self._entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{self._entry_id}_{key}"

    @property
    def available(self) -> bool:
        """Entities are unavailable while the socket is down."""
        return super().available and self.coordinator.data.connected


class BabyMonitarrGlobalEntity(BabyMonitarrEntity):
    """An entity on the hub device."""

    def __init__(self, coordinator: BabyMonitarrCoordinator, key: str) -> None:
        """Initialise and attach to the hub device."""
        super().__init__(coordinator, key)
        self._attr_device_info = global_device_info(self._entry_id, coordinator)


class BabyMonitarrRoomEntity(BabyMonitarrEntity):
    """An entity on a room device."""

    def __init__(
        self, coordinator: BabyMonitarrCoordinator, room_id: int, key: str
    ) -> None:
        """Initialise and attach to the room device."""
        super().__init__(coordinator, f"room_{room_id}_{key}")
        self.room_id = room_id
        room = coordinator.data.rooms[room_id]
        self._attr_device_info = room_device_info(self._entry_id, room)

    @property
    def room(self) -> RoomInfo | None:
        """The room, or None if it has disappeared from the room list."""
        return self.coordinator.data.rooms.get(self.room_id)

    @property
    def room_state(self) -> RoomState:
        """The room's live state."""
        return self.coordinator.data.state(self.room_id)

    @property
    def room_cast_state(self) -> CastRoomState:
        """The room's cast state."""
        return self.coordinator.data.cast_state(self.room_id)

    @property
    def available(self) -> bool:
        """Unavailable when the socket is down or the room is gone."""
        return super().available and self.room is not None
