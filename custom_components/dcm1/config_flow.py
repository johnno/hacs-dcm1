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

from .const import CONF_ENTITY_NAME_SUFFIX, CONF_USE_ZONE_LABELS, DEFAULT_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default="DCM1"): str,
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_USE_ZONE_LABELS, default=True): bool,
        vol.Optional(CONF_ENTITY_NAME_SUFFIX, default=""): str,
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


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""
