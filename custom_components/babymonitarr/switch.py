"""Switches for BabyMonitarr."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SETTING_AUDIO_FILTER_ENABLED
from .coordinator import BabyMonitarrCoordinator
from .entity import BabyMonitarrGlobalEntity, BabyMonitarrRoomEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the audio filter switch now and monitoring switches as rooms arrive."""
    coordinator: BabyMonitarrCoordinator = entry.runtime_data

    async_add_entities([BabyMonitarrAudioFilterSwitch(coordinator)])

    @callback
    def _add_rooms(room_ids: list[int]) -> None:
        async_add_entities(
            BabyMonitarrMonitoringSwitch(coordinator, room_id) for room_id in room_ids
        )

    coordinator.async_add_new_rooms_callback(_add_rooms)
    if coordinator.data.rooms:
        _add_rooms(list(coordinator.data.rooms))


class BabyMonitarrMonitoringSwitch(BabyMonitarrRoomEntity, SwitchEntity):
    """The always-on subscription for one room.

    The backend is the source of truth: monitoring survives a dropped connection
    but resets to off when the backend restarts, and the snapshot always tells the
    truth. So this switch reflects ``room_state.monitoring`` rather than restoring a
    remembered HA state - protocol section 5. It defaults off; that is intentional,
    because monitoring keeps the room's reader (and its ffmpeg pipeline) alive.
    """

    _attr_translation_key = "monitoring"

    def __init__(self, coordinator: BabyMonitarrCoordinator, room_id: int) -> None:
        """Initialise the switch."""
        super().__init__(coordinator, room_id, "monitoring")

    @property
    def is_on(self) -> bool:
        """Whether the backend reports monitoring on for this room."""
        return self.room_state.monitoring

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn monitoring on."""
        await self.coordinator.async_set_monitoring(self.room_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn monitoring off."""
        await self.coordinator.async_set_monitoring(self.room_id, False)


class BabyMonitarrAudioFilterSwitch(BabyMonitarrGlobalEntity, SwitchEntity):
    """The global audio filter. Global only; per-room overrides are out of scope."""

    _attr_translation_key = "audio_filter"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: BabyMonitarrCoordinator) -> None:
        """Initialise the switch."""
        super().__init__(coordinator, "audio_filter")

    @property
    def is_on(self) -> bool:
        """Whether the filter is enabled."""
        return bool(self.coordinator.data.settings.get(SETTING_AUDIO_FILTER_ENABLED))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the filter."""
        await self.coordinator.async_set_global_setting(
            SETTING_AUDIO_FILTER_ENABLED, True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the filter."""
        await self.coordinator.async_set_global_setting(
            SETTING_AUDIO_FILTER_ENABLED, False
        )
