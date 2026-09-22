"""Sensors for BabyMonitarr."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfSoundPressure
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import ATTR_CASTING_TO, ATTR_TARGET_IDS, ATTR_TARGET_NAMES
from .coordinator import (
    BabyMonitarrCoordinator,
    BabyMonitarrData,
    CastRoomState,
    RoomState,
)
from .entity import BabyMonitarrGlobalEntity, BabyMonitarrRoomEntity


@dataclass(frozen=True, kw_only=True)
class BabyMonitarrRoomSensorDescription(SensorEntityDescription):
    """Describes a per-room sensor.

    ``value_fn`` gets both the room's live state and its cast state, because the
    two arrive on separate messages (``room_state`` and ``cast.state``).
    """

    value_fn: Callable[[RoomState, CastRoomState], StateType]
    attributes_fn: (
        Callable[[BabyMonitarrCoordinator, RoomState, CastRoomState], dict[str, Any]]
        | None
    ) = None


@dataclass(frozen=True, kw_only=True)
class BabyMonitarrGlobalSensorDescription(SensorEntityDescription):
    """Describes a hub-wide sensor."""

    value_fn: Callable[[BabyMonitarrData], StateType]


ROOM_SENSORS: tuple[BabyMonitarrRoomSensorDescription, ...] = (
    BabyMonitarrRoomSensorDescription(
        key="sound_level",
        translation_key="sound_level",
        device_class=SensorDeviceClass.SOUND_PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfSoundPressure.DECIBEL,
        suggested_display_precision=1,
        value_fn=lambda state, cast: state.level_db,
    ),
    BabyMonitarrRoomSensorDescription(
        key="cast_targets",
        translation_key="cast_targets",
        # The state is the number of SAVED targets - a name list would blow past
        # the 255-character state limit on a house with several receivers. The
        # ids, names and live sessions are on the attributes.
        native_unit_of_measurement="targets",
        value_fn=lambda state, cast: len(cast.targets),
        attributes_fn=lambda coordinator, state, cast: {
            ATTR_TARGET_IDS: list(cast.targets),
            ATTR_TARGET_NAMES: [
                coordinator.data.device_name(device_id) for device_id in cast.targets
            ],
            ATTR_CASTING_TO: [
                coordinator.data.device_name(session.device_id)
                for session in cast.sessions
            ],
        },
    ),
)

GLOBAL_SENSORS: tuple[BabyMonitarrGlobalSensorDescription, ...] = (
    BabyMonitarrGlobalSensorDescription(
        key="active_room",
        translation_key="active_room",
        value_fn=lambda data: data.active_room_name,
    ),
    BabyMonitarrGlobalSensorDescription(
        key="connected_viewers",
        translation_key="connected_viewers",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.connected_viewers,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the global sensors now and the per-room sensors as rooms arrive."""
    coordinator: BabyMonitarrCoordinator = entry.runtime_data

    async_add_entities(
        BabyMonitarrGlobalSensor(coordinator, description)
        for description in GLOBAL_SENSORS
    )

    @callback
    def _add_rooms(room_ids: list[int]) -> None:
        async_add_entities(
            BabyMonitarrRoomSensor(coordinator, room_id, description)
            for room_id in room_ids
            for description in ROOM_SENSORS
        )

    coordinator.async_add_new_rooms_callback(_add_rooms)
    if coordinator.data.rooms:
        _add_rooms(list(coordinator.data.rooms))


class BabyMonitarrRoomSensor(BabyMonitarrRoomEntity, SensorEntity):
    """A per-room sensor."""

    entity_description: BabyMonitarrRoomSensorDescription

    def __init__(
        self,
        coordinator: BabyMonitarrCoordinator,
        room_id: int,
        description: BabyMonitarrRoomSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, room_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Return the current value."""
        return self.entity_description.value_fn(
            self.room_state, self.room_cast_state
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the description's extra attributes, if it has any."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(
            self.coordinator, self.room_state, self.room_cast_state
        )


class BabyMonitarrGlobalSensor(BabyMonitarrGlobalEntity, SensorEntity):
    """A hub-wide sensor."""

    entity_description: BabyMonitarrGlobalSensorDescription

    def __init__(
        self,
        coordinator: BabyMonitarrCoordinator,
        description: BabyMonitarrGlobalSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Return the current value."""
        return self.entity_description.value_fn(self.coordinator.data)
