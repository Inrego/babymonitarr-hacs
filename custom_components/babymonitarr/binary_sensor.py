"""Binary sensors for BabyMonitarr."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import BabyMonitarrCoordinator, CastRoomState, RoomState
from .entity import BabyMonitarrRoomEntity


@dataclass(frozen=True, kw_only=True)
class BabyMonitarrBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a per-room binary sensor.

    ``value_fn`` gets both the room's live state and its cast state, because the
    two arrive on separate messages (``room_state`` and ``cast.state``).
    """

    value_fn: Callable[[RoomState, CastRoomState], bool]


ROOM_BINARY_SENSORS: tuple[BabyMonitarrBinarySensorDescription, ...] = (
    BabyMonitarrBinarySensorDescription(
        key="sound",
        translation_key="sound",
        device_class=BinarySensorDeviceClass.SOUND,
        # The 30 s clear-hold is applied server-side; this is the held state.
        value_fn=lambda state, cast: state.sound_detected,
    ),
    BabyMonitarrBinarySensorDescription(
        key="stream_online",
        translation_key="stream_online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda state, cast: state.stream_online,
    ),
    BabyMonitarrBinarySensorDescription(
        key="casting",
        translation_key="casting",
        # True when at least one cast session is live; a saved target with no
        # session is not casting.
        value_fn=lambda state, cast: cast.casting,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensors, adding more as rooms arrive."""
    coordinator: BabyMonitarrCoordinator = entry.runtime_data

    @callback
    def _add_rooms(room_ids: list[int]) -> None:
        async_add_entities(
            BabyMonitarrRoomBinarySensor(coordinator, room_id, description)
            for room_id in room_ids
            for description in ROOM_BINARY_SENSORS
        )

    coordinator.async_add_new_rooms_callback(_add_rooms)
    if coordinator.data.rooms:
        _add_rooms(list(coordinator.data.rooms))


class BabyMonitarrRoomBinarySensor(BabyMonitarrRoomEntity, BinarySensorEntity):
    """A per-room binary sensor."""

    entity_description: BabyMonitarrBinarySensorDescription

    def __init__(
        self,
        coordinator: BabyMonitarrCoordinator,
        room_id: int,
        description: BabyMonitarrBinarySensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, room_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        """Return the current state."""
        return self.entity_description.value_fn(
            self.room_state, self.room_cast_state
        )
