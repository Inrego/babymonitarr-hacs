"""Services for BabyMonitarr.

``cast_room`` and ``stop_cast`` drive the backend's ``cast.*`` commands.
``snapshot`` is registered but always fails: see :func:`_async_snapshot`.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_ROOM,
    ATTR_TARGETS,
    DOMAIN,
    SERVICE_CAST_ROOM,
    SERVICE_SNAPSHOT,
    SERVICE_STOP_CAST,
)
from .coordinator import BabyMonitarrCoordinator

_LOGGER = logging.getLogger(__name__)

CAST_ROOM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ROOM): cv.string,
        vol.Optional(ATTR_TARGETS): vol.All(cv.ensure_list, [cv.string]),
    }
)

STOP_CAST_SCHEMA = vol.Schema({vol.Required(ATTR_ROOM): cv.string})

SNAPSHOT_SCHEMA = vol.Schema({vol.Required(ATTR_ROOM): cv.string})


def _coordinator(hass: HomeAssistant) -> BabyMonitarrCoordinator:
    """The single hub's coordinator, or a clear error if there is not one."""
    entries = [
        entry
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]
    if not entries:
        raise ServiceValidationError("BabyMonitarr is not set up")
    return entries[0].runtime_data


def _require_cast(coordinator: BabyMonitarrCoordinator) -> None:
    """Refuse a cast command a backend has not advertised support for."""
    if not coordinator.data.cast_supported:
        raise HomeAssistantError(
            "This BabyMonitarr backend does not advertise the 'cast' feature"
        )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's services. Called once, from async_setup."""

    async def _async_cast_room(call: ServiceCall) -> None:
        """Start casting a room to the given targets, or its saved ones."""
        coordinator = _coordinator(hass)
        _require_cast(coordinator)
        room_id = coordinator.resolve_room(call.data[ATTR_ROOM])
        targets = call.data.get(ATTR_TARGETS)
        device_ids = coordinator.resolve_targets(targets) if targets else None

        result = await coordinator.async_cast_start(room_id, device_ids)
        failed: dict[str, Any] = result.get("failed") or {}
        started = result.get("started") or []
        if failed:
            # A partial failure is a success at the protocol level, so nothing
            # below us raises - but the user asked for these devices and must be
            # told which ones did not start.
            detail = "; ".join(
                f"{coordinator.data.device_name(device_id)}: {reason}"
                for device_id, reason in failed.items()
            )
            raise HomeAssistantError(
                f"{len(started)} device(s) started, {len(failed)} failed - {detail}"
            )
        if not started:
            raise HomeAssistantError(
                "No Cast devices were started. The room has no saved targets and "
                "none were given."
            )

    async def _async_stop_cast(call: ServiceCall) -> None:
        """Stop every cast session for a room."""
        coordinator = _coordinator(hass)
        _require_cast(coordinator)
        await coordinator.async_cast_stop(coordinator.resolve_room(call.data[ATTR_ROOM]))

    async def _async_snapshot(call: ServiceCall) -> None:
        """Always fail, with the reason.

        The backend has no still-image capability at all: `FfprobeSnapshotService`
        only writes stream metadata to the log, and nothing encodes a frame to
        JPEG or PNG (protocol doc, section 8.1). The service is registered rather
        than omitted so that an automation author gets this explanation instead of
        an ambiguous "service not found", and so the name is reserved for when a
        real still endpoint exists.
        """
        coordinator = _coordinator(hass)
        # Validate the room anyway, so a typo is reported as a typo.
        coordinator.resolve_room(call.data[ATTR_ROOM])
        raise HomeAssistantError(
            "babymonitarr.snapshot is not supported by this backend: BabyMonitarr "
            "has no still-image capability. Nothing was captured."
        )

    hass.services.async_register(
        DOMAIN, SERVICE_CAST_ROOM, _async_cast_room, schema=CAST_ROOM_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_STOP_CAST, _async_stop_cast, schema=STOP_CAST_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SNAPSHOT, _async_snapshot, schema=SNAPSHOT_SCHEMA
    )
