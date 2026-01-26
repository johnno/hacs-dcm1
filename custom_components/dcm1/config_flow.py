"""Config flow for Cloud DCM1 Zone Mixer integration."""

from __future__ import annotations

from asyncio import timeout
import logging
from typing import Any

from pydcm1.mixer import DCM1Mixer
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_ENTITY_NAME_SUFFIX,
    CONF_OPTIMISTIC_VOLUME,
    CONF_USE_ZONE_LABELS,
    CONF_VOLUME_DB_RANGE,
    DEFAULT_PORT,
    DEFAULT_VOLUME_DB_RANGE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default="DCM1"): str,
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_USE_ZONE_LABELS, default=True): bool,
        vol.Optional(CONF_OPTIMISTIC_VOLUME, default=True): bool,
        vol.Optional(CONF_ENTITY_NAME_SUFFIX, default=""): str,
        vol.Optional(CONF_VOLUME_DB_RANGE, default=DEFAULT_VOLUME_DB_RANGE): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=61)
        ),
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """

    mixer = DCM1Mixer(hostname=data[CONF_HOST], port=data[CONF_PORT])

    try:
        async with timeout(5.0):
            await mixer.async_connect()
    except (ConnectionRefusedError, TimeoutError, ConnectionResetError) as exp:
        _LOGGER.exception("Error connecting to DCM1 Mixer")
        mixer.close()
        raise CannotConnect from exp

    # Return info that you want to store in the config entry.
    return {"title": data[CONF_NAME]}


class ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Cloud DCM1 Zone Mixer."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the integration."""
        config_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if config_entry is None:
            return self.async_abort(reason="reconfigure_failed")

        if user_input is not None:
            # Update the config entry with new values, keeping existing host/port/name
            config_entry.data = {**config_entry.data, **user_input}
            self.hass.config_entries.async_update_entry(config_entry)
            await self.hass.config_entries.async_reload(config_entry.entry_id)
            return self.async_abort_entry_setup_complete()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_OPTIMISTIC_VOLUME,
                        default=config_entry.data.get(CONF_OPTIMISTIC_VOLUME, True),
                    ): bool,
                    vol.Optional(
                        CONF_ENTITY_NAME_SUFFIX,
                        default=config_entry.data.get(CONF_ENTITY_NAME_SUFFIX, ""),
                    ): str,
                    vol.Optional(
                        CONF_VOLUME_DB_RANGE,
                        default=config_entry.data.get(
                            CONF_VOLUME_DB_RANGE, DEFAULT_VOLUME_DB_RANGE
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=61)),
                }
            ),
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""
