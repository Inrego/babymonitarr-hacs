"""mDNS proxy: Home Assistant browses for Chromecasts on the backend's behalf.

mDNS is link-local. The backend runs on a Docker bridge network and never sees the
multicast, so its own ``_googlecast._tcp.local.`` browse finds nothing. Home
Assistant is already on the LAN with a Zeroconf browser running, so it browses and
pushes what it finds over ``cast.discovered`` - protocol section 8.2.

Only discovery needs multicast. The backend keeps connecting to port 8009 directly
with Sharpcaster, so nothing about the media path changes.

This uses Home Assistant's *shared* Zeroconf instance. Standing up a second one
would double the multicast traffic and is exactly what the shared instance exists
to prevent.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import zeroconf as ha_zeroconf
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.debounce import Debouncer
from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from .api import BabyMonitarrError
from .const import (
    CAST_PUSH_DEBOUNCE_SECONDS,
    CAST_SERVICE_TYPE,
    DEFAULT_CAST_PORT,
    TXT_CAPABILITIES,
    TXT_FRIENDLY_NAME,
    TXT_ID,
    TXT_MODEL,
)
from .coordinator import BabyMonitarrCoordinator

_LOGGER = logging.getLogger(__name__)

# How long to wait for a service's address and TXT record to resolve.
_RESOLVE_TIMEOUT_MS = 3000


def _decode(value: Any) -> str | None:
    """Decode one mDNS TXT value, which arrives as bytes."""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8").strip()
        except UnicodeDecodeError:
            return None
    if isinstance(value, str):
        return value.strip()
    return None


def build_device(info: AsyncServiceInfo) -> dict[str, Any] | None:
    """Turn a resolved service into a ``cast.discovered`` device entry.

    Extracts exactly the fields the backend already parses: the address, the port
    and the TXT ``id``/``fn``/``md``/``ca``. Returns None when the record has no
    usable identity or address, which the backend would skip anyway.
    """
    properties: dict[str, str] = {}
    for raw_key, raw_value in (info.properties or {}).items():
        key = _decode(raw_key)
        value = _decode(raw_value)
        if key:
            properties[key] = value or ""

    device_id = properties.get(TXT_ID)
    if not device_id:
        # No TXT "id" means no identity the backend can dedupe on.
        return None

    addresses = info.parsed_scoped_addresses() or []
    if not addresses:
        return None

    device: dict[str, Any] = {
        "id": device_id,
        "host": addresses[0],
        "port": info.port or DEFAULT_CAST_PORT,
    }
    for txt_key in (TXT_FRIENDLY_NAME, TXT_MODEL, TXT_CAPABILITIES):
        value = properties.get(txt_key)
        if value:
            device[txt_key] = value
    return device


class BabyMonitarrCastProxy:
    """Browses for Cast receivers and pushes them to the backend."""

    def __init__(
        self, hass: HomeAssistant, coordinator: BabyMonitarrCoordinator
    ) -> None:
        """Initialise the proxy. Nothing starts until :meth:`async_start`."""
        self.hass = hass
        self.coordinator = coordinator
        self._aiozc: AsyncZeroconf | None = None
        self._browser: AsyncServiceBrowser | None = None
        self._unsub_snapshot: CALLBACK_TYPE | None = None
        # Keyed by mDNS service name, valued by the payload we last built for it.
        self._devices: dict[str, dict[str, Any]] = {}
        self._debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=CAST_PUSH_DEBOUNCE_SECONDS,
            immediate=False,
            function=self._async_push,
        )

    async def async_start(self) -> None:
        """Take HA's shared browser and start watching for receivers."""
        self._aiozc = await ha_zeroconf.async_get_async_instance(self.hass)
        self._browser = AsyncServiceBrowser(
            self._aiozc.zeroconf,
            [CAST_SERVICE_TYPE],
            handlers=[self._handle_service_state_change],
        )
        # A backend restart forgets nothing about devices, but a reconnect is the
        # cheapest moment to refresh their addresses - protocol section 5.
        self._unsub_snapshot = self.coordinator.async_add_snapshot_callback(
            self._handle_snapshot
        )
        _LOGGER.debug("Cast proxy browsing %s", CAST_SERVICE_TYPE)

    async def async_stop(self) -> None:
        """Stop browsing. The shared Zeroconf instance is left alone."""
        if self._unsub_snapshot is not None:
            self._unsub_snapshot()
            self._unsub_snapshot = None
        await self._debouncer.async_shutdown()
        if self._browser is not None:
            await self._browser.async_cancel()
            self._browser = None
        self._aiozc = None
        self._devices.clear()

    @callback
    def _handle_snapshot(self) -> None:
        """Re-push everything we know after a reconnect."""
        if self._devices:
            self.hass.async_create_task(self._async_push())

    @callback
    def _handle_service_state_change(
        self,
        zeroconf: Any,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        """React to a receiver appearing, moving or going away.

        Chromecast addresses move on DHCP renewal, so an update is as important as
        an add: the backend upserts on the TXT ``id`` and corrects the stored
        address in place.
        """
        if state_change is ServiceStateChange.Removed:
            if self._devices.pop(name, None) is not None:
                # There is no unregister message; a receiver we stop refreshing
                # simply decays to is_online: false on the backend.
                _LOGGER.debug("Cast receiver %s went away", name)
            return

        self.hass.async_create_task(self._async_resolve(service_type, name))

    async def _async_resolve(self, service_type: str, name: str) -> None:
        """Resolve one service and queue a push if anything changed."""
        if self._aiozc is None:
            return
        info = AsyncServiceInfo(service_type, name)
        if not await info.async_request(self._aiozc.zeroconf, _RESOLVE_TIMEOUT_MS):
            _LOGGER.debug("Cast receiver %s did not resolve", name)
            return

        device = build_device(info)
        if device is None:
            _LOGGER.debug("Ignoring %s: no usable TXT id or address", name)
            return

        if self._devices.get(name) == device:
            return
        self._devices[name] = device
        await self._debouncer.async_call()

    async def _async_push(self) -> None:
        """Push the whole current set to the backend.

        Always the full set, never a delta: the backend upserts and never removes
        on absence.
        """
        if not self._devices:
            return
        if not self.coordinator.data.cast_supported:
            _LOGGER.debug("Backend does not advertise the cast feature; not pushing")
            return
        devices = list(self._devices.values())
        try:
            await self.coordinator.async_cast_discovered(devices)
        except BabyMonitarrError as err:
            # The next add, update or reconnect pushes again; nothing is lost.
            _LOGGER.debug("Could not push %d Cast receiver(s): %s", len(devices), err)
        else:
            _LOGGER.debug("Pushed %d Cast receiver(s) to the backend", len(devices))
