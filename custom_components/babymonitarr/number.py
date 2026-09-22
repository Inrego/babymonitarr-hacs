"""Numbers for BabyMonitarr's global audio settings.

These are global and apply to every room; per-room overrides are explicitly out of
scope for protocol v1.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfSoundPressure, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    SETTING_SOUND_THRESHOLD_DB,
    SETTING_THRESHOLD_PAUSE_SECONDS,
    SETTING_VOLUME_ADJUSTMENT_DB,
)
from .coordinator import BabyMonitarrCoordinator
from .entity import BabyMonitarrGlobalEntity


@dataclass(frozen=True, kw_only=True)
class BabyMonitarrNumberDescription(NumberEntityDescription):
    """Describes a global settings number.

    ``setting`` is the ``global_settings`` field name, verbatim - it is what
    ``set_global_settings`` writes.
    """

    setting: str
    integer: bool = False


# The min/max/step below are UI affordances, not protocol constraints: the
# protocol gives types, not ranges. The backend validates.
NUMBERS: tuple[BabyMonitarrNumberDescription, ...] = (
    BabyMonitarrNumberDescription(
        key="sound_threshold",
        translation_key="sound_threshold",
        setting=SETTING_SOUND_THRESHOLD_DB,
        device_class=NumberDeviceClass.SOUND_PRESSURE,
        native_unit_of_measurement=UnitOfSoundPressure.DECIBEL,
        native_min_value=-90,
        native_max_value=0,
        native_step=0.5,
        mode=NumberMode.BOX,
    ),
    BabyMonitarrNumberDescription(
        key="threshold_pause",
        translation_key="threshold_pause",
        setting=SETTING_THRESHOLD_PAUSE_SECONDS,
        integer=True,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_min_value=0,
        native_max_value=600,
        native_step=1,
        mode=NumberMode.BOX,
    ),
    BabyMonitarrNumberDescription(
        key="volume_adjustment",
        translation_key="volume_adjustment",
        setting=SETTING_VOLUME_ADJUSTMENT_DB,
        native_unit_of_measurement=UnitOfSoundPressure.DECIBEL,
        native_min_value=-60,
        native_max_value=20,
        native_step=0.5,
        mode=NumberMode.BOX,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the global settings numbers."""
    coordinator: BabyMonitarrCoordinator = entry.runtime_data
    async_add_entities(
        BabyMonitarrNumber(coordinator, description) for description in NUMBERS
    )


class BabyMonitarrNumber(BabyMonitarrGlobalEntity, NumberEntity):
    """One global audio setting."""

    entity_description: BabyMonitarrNumberDescription
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: BabyMonitarrCoordinator,
        description: BabyMonitarrNumberDescription,
    ) -> None:
        """Initialise the number."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        """Return the value the backend last reported."""
        value = self.coordinator.data.settings.get(self.entity_description.setting)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    async def async_set_native_value(self, value: float) -> None:
        """Write the setting back to the backend."""
        payload: float | int = (
            round(value) if self.entity_description.integer else value
        )
        await self.coordinator.async_set_global_setting(
            self.entity_description.setting, payload
        )
