"""Sensors for BabyMonitarr."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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

from .coordinator import BabyMonitarrCoordinator, BabyMonitarrData, RoomState
from .entity import BabyMonitarrGlobalEntity, BabyMonitarrRoomEntity


@dataclass(frozen=True, kw_only=True)
class BabyMonitarrRoomSensorDescription(SensorEntityDescription):
    """Describes a per-room sensor."""

    value_fn: Callable[[RoomState], StateType]


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
        value_fn=lambda state: state.level_db,
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
        return self.entity_description.value_fn(self.room_state)


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
