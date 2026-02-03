"""The Cloud DCM1 Zone Mixer integration."""

from __future__ import annotations

from asyncio import timeout
import logging

from pydcm1.listener import LoggingListener
from pydcm1.mixer import DCM1Mixer

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER, Platform.NUMBER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Cloud DCM1 Zone Mixer from a config entry."""
    hostname = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]

    mixer = DCM1Mixer(hostname, port)
    try:
        mixer.register_listener(LoggingListener())
        async with timeout(5.0):
            await mixer.async_connect()
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = mixer

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    except (ConnectionRefusedError, TimeoutError, ConnectionResetError):
        _LOGGER.exception("Error connecting to DCM1 Mixer")
    else:
        return True
    return False


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    mixer: DCM1Mixer = hass.data[DOMAIN][entry.entry_id]
    mixer.close()

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reconfigure_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle reconfiguration of the entry."""
    await hass.config_entries.async_reload(entry.entry_id)
