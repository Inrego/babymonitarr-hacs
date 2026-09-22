"""The BabyMonitarr integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    BabyMonitarrAuthError,
    BabyMonitarrClient,
    BabyMonitarrConnectionError,
    normalise_ws_url,
)
from .const import CONF_API_KEY, CONF_HOST
from .coordinator import BabyMonitarrCoordinator

# The camera platform (native WebRTC over webrtc.*) lands in a later task and
# joins this list then.
PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

type BabyMonitarrConfigEntry = ConfigEntry[BabyMonitarrCoordinator]


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

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: BabyMonitarrConfigEntry
) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.client.async_stop()
    return unload_ok
