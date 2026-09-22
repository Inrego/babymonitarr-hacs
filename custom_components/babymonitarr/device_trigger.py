"""Device triggers for BabyMonitarr room devices.

One trigger per room device, backed by the ``babymonitarr_sound_detected`` bus
event the coordinator fires on every discrete threshold crossing. The event is the
right source here rather than the binary sensor: the sensor latches for the
server-side clear hold, so repeat crossings while it is already on produce no state
change - but they do produce an event.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import InvalidDeviceAutomationConfig
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, EVENT_SOUND_DETECTED, TRIGGER_SOUND_DETECTED

TRIGGER_TYPES = {TRIGGER_SOUND_DETECTED}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES)}
)


def _room_id_for_device(hass: HomeAssistant, device_id: str) -> int | None:
    """Recover the room id from a device's identifiers, or None for the hub."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    for domain, identifier in device.identifiers:
        if domain != DOMAIN:
            continue
        # Room devices are identified as "<entry_id>_room_<room_id>".
        _, separator, room_id = identifier.partition("_room_")
        if separator and room_id.isdigit():
            return int(room_id)
    return None


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """Offer the sound trigger on room devices only."""
    if _room_id_for_device(hass, device_id) is None:
        return []
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: TRIGGER_SOUND_DETECTED,
        }
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach an event trigger filtered to this device's room."""
    room_id = _room_id_for_device(hass, config[CONF_DEVICE_ID])
    if room_id is None:
        raise InvalidDeviceAutomationConfig(
            f"Device {config[CONF_DEVICE_ID]} is not a BabyMonitarr room"
        )

    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: EVENT_SOUND_DETECTED,
            event_trigger.CONF_EVENT_DATA: {"room_id": room_id},
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
