"""Config flow for BabyMonitarr."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    BabyMonitarrAuthError,
    BabyMonitarrClient,
    BabyMonitarrConnectionError,
    normalise_ws_url,
)
from .const import CONF_API_KEY, CONF_HOST, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_API_KEY): str,
    }
)

STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): str})


class BabyMonitarrConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the user-initiated setup of the BabyMonitarr hub."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the backend address and an API key, then handshake."""
        # A hub: one backend, one entry.
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                url = normalise_ws_url(user_input[CONF_HOST])
            except ValueError:
                errors["base"] = "invalid_host"
            else:
                client = BabyMonitarrClient(
                    async_get_clientsession(self.hass),
                    url,
                    user_input[CONF_API_KEY],
                )
                try:
                    hello = await client.async_validate()
                except BabyMonitarrAuthError:
                    errors["base"] = "invalid_auth"
                except BabyMonitarrConnectionError as err:
                    _LOGGER.debug("Cannot connect to %s: %s", url, err)
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error validating BabyMonitarr")
                    errors["base"] = "unknown"
                else:
                    await self.async_set_unique_id(DOMAIN)
                    self._abort_if_unique_id_configured()
                    _LOGGER.debug(
                        "Connected to BabyMonitarr %s", hello.get("server_version")
                    )
                    return self.async_create_entry(
                        title="BabyMonitarr", data=user_input
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input or {}
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle a rejected API key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new API key against the configured host."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            url = normalise_ws_url(entry.data[CONF_HOST])
            client = BabyMonitarrClient(
                async_get_clientsession(self.hass), url, user_input[CONF_API_KEY]
            )
            try:
                await client.async_validate()
            except BabyMonitarrAuthError:
                errors["base"] = "invalid_auth"
            except BabyMonitarrConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_KEY: user_input[CONF_API_KEY]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
        )
