"""The BabyMonitarr integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import (
    BabyMonitarrAuthError,
    BabyMonitarrClient,
    BabyMonitarrConnectionError,
    normalise_ws_url,
)
from .cast_proxy import BabyMonitarrCastProxy
from .const import CONF_API_KEY, CONF_HOST, DOMAIN
from .coordinator import BabyMonitarrCoordinator
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

type BabyMonitarrConfigEntry = ConfigEntry[BabyMonitarrCoordinator]

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's services once, ahead of any config entry."""
    async_setup_services(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: BabyMonitarrConfigEntry
) -> bool:
    """Set up BabyMonitarr from a config entry."""
    try:
        url = normalise_ws_url(entry.data[CONF_HOST])
    except ValueError as err:
        # A malformed host will not fix itself on a retry.
        raise ConfigEntryError(str(err)) from err

    client = BabyMonitarrClient(
        async_get_clientsession(hass), url, entry.data[CONF_API_KEY]
    )
    coordinator = BabyMonitarrCoordinator(hass, entry, client)

    try:
        # The backend pushes a full snapshot on connect, so this is all the
        # priming there is - there is nothing to poll.
        await client.async_start()
    except BabyMonitarrAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except BabyMonitarrConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    try:
        # Everything the entities need arrives in one snapshot, so wait for it
        # rather than creating entities against an empty state.
        await coordinator.async_wait_for_snapshot()
    except TimeoutError as err:
        await client.async_stop()
        raise ConfigEntryNotReady(
            "Timed out waiting for the BabyMonitarr snapshot"
        ) from err

    entry.runtime_data = coordinator

    # HA is the mDNS proxy: the backend's own browse cannot see link-local
    # multicast from a Docker bridge network. Only start it if the backend says
    # it speaks cast.
    if coordinator.data.cast_supported:
        proxy = BabyMonitarrCastProxy(hass, coordinator)
        await proxy.async_start()
        coordinator.cast_proxy = proxy
    else:
        _LOGGER.debug("Backend does not advertise 'cast'; mDNS proxy not started")

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: BabyMonitarrConfigEntry
) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = entry.runtime_data
        if coordinator.cast_proxy is not None:
            await coordinator.cast_proxy.async_stop()
            coordinator.cast_proxy = None
        await coordinator.client.async_stop()
    return unload_ok
