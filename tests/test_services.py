"""Services: the cast services end to end, including the target loop.

The point of the ``set_cast_targets`` cases is that the loop closes: the service
sends ``cast.set_targets``, the backend answers with an ack plus a broadcast
``cast.state`` (protocol doc, section "cast.set_targets"), and the room's
``cast_targets`` sensor reflects the new selection. A fix that set targets but
left the sensor reading 0 would not be one.

Run directly: ``python tests/test_services.py``. No Home Assistant install needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stubs

stubs.install()

import asyncio
from types import SimpleNamespace

from custom_components.babymonitarr.const import DOMAIN
from custom_components.babymonitarr.coordinator import BabyMonitarrCoordinator
from custom_components.babymonitarr.sensor import ROOM_SENSORS
from custom_components.babymonitarr.services import async_setup_services

results = stubs.Results("services")
check = results.check

CAST_TARGETS = next(d for d in ROOM_SENSORS if d.key == "cast_targets")


class _FakeBackend:
    """Stands in for the client, and broadcasts what the backend broadcasts."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.coordinator: BabyMonitarrCoordinator | None = None
        self.error: dict | None = None

    async def async_cast_set_targets(self, room_id, device_ids):
        self.calls.append(("cast.set_targets", (room_id, list(device_ids))))
        if self.error is not None:
            return ("error", self.error)
        # Ack plus the broadcast cast.state the real backend sends with it.
        self.coordinator.handle_message(
            "cast.state",
            {"room_id": room_id, "casting": False, "targets": list(device_ids),
             "sessions": []},
            None,
        )
        return ("ack", {"command": "cast.set_targets"})


def _setup() -> tuple[stubs.Hass, BabyMonitarrCoordinator, _FakeBackend]:
    """A hub with two rooms, two receivers and the services registered."""
    hass = stubs.Hass()
    entry = stubs.ConfigEntryStub()
    backend = _FakeBackend()
    coordinator = BabyMonitarrCoordinator(
        hass, entry, SimpleNamespace(set_callbacks=lambda a, b: None)
    )
    coordinator.client = backend
    backend.coordinator = coordinator
    entry.runtime_data = coordinator
    hass.config_entries.entries.append(entry)

    coordinator.handle_message(
        "hello", {"server_version": "1.5.0", "features": ["rooms", "cast"]}, None
    )
    coordinator.handle_message(
        "rooms",
        {"rooms": [{"id": 1, "name": "Nursery"}, {"id": 2, "name": "Guest Room"}]},
        None,
    )
    coordinator.handle_message(
        "cast.devices",
        {"devices": [
            {"device_id": "1f2e", "name": "Living Room TV", "is_online": True},
            {"device_id": "9a8b", "name": "Kitchen Speaker", "is_online": True},
        ]},
        None,
    )
    coordinator.handle_message(
        "cast.state",
        {"room_id": 1, "casting": False, "targets": [], "sessions": []},
        None,
    )
    async_setup_services(hass)
    return hass, coordinator, backend


def _sensor(coordinator: BabyMonitarrCoordinator, room_id: int):
    """The room's real cast_targets sensor entity."""
    from custom_components.babymonitarr.sensor import BabyMonitarrRoomSensor

    return BabyMonitarrRoomSensor(coordinator, room_id, CAST_TARGETS)


async def _main() -> None:
    hass, c, backend = _setup()

    check("set_cast_targets registered", hass.services.has_service(
        DOMAIN, "set_cast_targets"), True)

    sensor = _sensor(c, 1)
    check("sensor starts at 0 targets", sensor.native_value, 0)
    check(
        "available targets listed for the picker",
        sensor.extra_state_attributes["available_targets"],
        ["Kitchen Speaker", "Living Room TV"],
    )

    # --- the loop: names in, device ids on the wire, sensor follows ---------
    await hass.services.async_call(
        DOMAIN, "set_cast_targets",
        {"room": "Nursery", "targets": ["Living Room TV", "9a8b"]},
    )
    check("names and ids resolved to device ids", backend.calls[-1],
          ("cast.set_targets", (1, ["1f2e", "9a8b"])))
    check("sensor reflects the new targets", sensor.native_value, 2)
    attrs = sensor.extra_state_attributes
    check("sensor target ids", attrs["target_ids"], ["1f2e", "9a8b"])
    check("sensor target names", attrs["target_names"],
          ["Living Room TV", "Kitchen Speaker"])
    check("nothing started", attrs["casting_to"], [])

    # An empty list clears the selection, and the sensor goes back to 0.
    await hass.services.async_call(
        DOMAIN, "set_cast_targets", {"room": "Nursery", "targets": []}
    )
    check("cleared on the wire", backend.calls[-1], ("cast.set_targets", (1, [])))
    check("sensor back to 0", sensor.native_value, 0)

    # A room that has never had a cast.state still works.
    await hass.services.async_call(
        DOMAIN, "set_cast_targets", {"room": "2", "targets": ["Kitchen Speaker"]}
    )
    check("room by id", backend.calls[-1], ("cast.set_targets", (2, ["9a8b"])))
    check("second room's sensor", _sensor(c, 2).native_value, 1)

    # --- validation ---------------------------------------------------------
    before = len(backend.calls)
    await results.expect_raises_async(
        "unknown room", stubs.ServiceValidationError,
        hass.services.async_call(
            DOMAIN, "set_cast_targets", {"room": "Attic", "targets": ["9a8b"]}),
    )
    await results.expect_raises_async(
        "unknown target", stubs.ServiceValidationError,
        hass.services.async_call(
            DOMAIN, "set_cast_targets", {"room": "Nursery", "targets": ["Attic TV"]}),
    )
    check("nothing reached the backend", len(backend.calls), before)

    # --- the backend's own message is what the user sees --------------------
    backend.error = {"code": "unknown_room", "message": "No room 1"}
    try:
        await hass.services.async_call(
            DOMAIN, "set_cast_targets", {"room": "Nursery", "targets": ["9a8b"]}
        )
        results.failures.append("backend error: nothing raised")
    except stubs.HomeAssistantError as err:
        if "unknown_room" not in str(err) or "No room 1" not in str(err):
            results.failures.append(f"backend error: lost the detail, got {err!r}")
    backend.error = None

    # --- a backend without the cast feature refuses before sending ----------
    hass2, c2, backend2 = _setup()
    c2.data.features = ["rooms"]
    before = len(backend2.calls)
    await results.expect_raises_async(
        "cast feature not advertised", stubs.HomeAssistantError,
        hass2.services.async_call(
            DOMAIN, "set_cast_targets", {"room": "Nursery", "targets": ["9a8b"]}),
    )
    check("nothing sent without the feature", len(backend2.calls), before)

    # --- no loaded entry at all ---------------------------------------------
    hass3 = stubs.Hass()
    async_setup_services(hass3)
    await results.expect_raises_async(
        "not set up", stubs.ServiceValidationError,
        hass3.services.async_call(
            DOMAIN, "set_cast_targets", {"room": "Nursery", "targets": ["9a8b"]}),
    )


asyncio.run(_main())

sys.exit(results.report())
