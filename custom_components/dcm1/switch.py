"""Platform for switch integration — per-zone paging destination toggles."""

from __future__ import annotations

import logging

from pydcm1.mixer import DCM1Mixer

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ENTITY_NAME_SUFFIX, CONF_USE_ZONE_LABELS, DOMAIN, SIGNAL_PAGING_FLAGS_CHANGED

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add a PagingZoneSwitch for each zone, working from pydcm1 data directly."""
    mixer: DCM1Mixer = hass.data[DOMAIN][config_entry.entry_id]
    paging_flags: dict[int, bool] = hass.data[DOMAIN][f"{config_entry.entry_id}_paging_flags"]
    use_zone_labels = config_entry.data.get(CONF_USE_ZONE_LABELS, True)
    entity_name_suffix = config_entry.data.get(CONF_ENTITY_NAME_SUFFIX, "")

    _LOGGER.debug("Waiting for zone data before creating PagingZoneSwitch entities...")
    zones_loaded = await mixer.wait_for_zone_data(timeout=12.0)
    if not zones_loaded:
        _LOGGER.warning("Timeout waiting for zone data — PagingZoneSwitch entities may use generic names")

    switches = []
    for zone_id, zone in mixer.zones_by_id.items():
        # Seed default if media_player hasn't set it yet (or if we ran first)
        if zone_id not in paging_flags:
            paging_flags[zone_id] = True
        switches.append(PagingZoneSwitch(
            zone_id=zone_id,
            zone_name=zone.name,
            mixer=mixer,
            paging_flags=paging_flags,
            entry_id=config_entry.entry_id,
            use_zone_labels=use_zone_labels,
            entity_name_suffix=entity_name_suffix,
        ))

    _LOGGER.debug("Setting up %s PagingZoneSwitch entities", len(switches))
    async_add_entities(switches)


class PagingZoneSwitch(SwitchEntity):
    """A switch that controls whether a zone is included in the next paging bus page.

    Works entirely from pydcm1 data — no dependency on the media_player platform's
    MixerZone entities.  State is stored in a shared paging_flags dict (pre-created
    in __init__.py) and a dispatcher signal is fired on every change so the PagingBus
    entity can update its 'source' display attribute in real time.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        zone_id: int,
        zone_name: str,
        mixer: DCM1Mixer,
        paging_flags: dict[int, bool],
        entry_id: str,
        use_zone_labels: bool,
        entity_name_suffix: str,
    ) -> None:
        self._zone_id = zone_id
        self._zone_name = zone_name
        self._mixer = mixer
        self._paging_flags = paging_flags
        self._entry_id = entry_id
        self._use_zone_labels = use_zone_labels
        self._entity_name_suffix = entity_name_suffix
        unique_base = f"dcm1_{mixer._hostname.replace('.', '_')}"
        self._zone_unique_id = f"{unique_base}_zone{zone_id}"
        self._attr_unique_id = f"{entry_id}_zone_{zone_id}_page_ready"
        self._attr_name = "Page Ready"

    async def async_added_to_hass(self) -> None:
        """Subscribe so we refresh if flags are changed externally (e.g. auto-reset after page)."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_PAGING_FLAGS_CHANGED.format(self._entry_id),
                self.schedule_update_ha_state,
            )
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Match the zone's media player device identifiers so we appear on the same card."""
        display_name = self._zone_name if self._use_zone_labels and self._zone_name else f"Zone {self._zone_id}"
        if self._entity_name_suffix:
            display_name = f"{display_name} {self._entity_name_suffix}"
        return DeviceInfo(
            identifiers={(DOMAIN, self._zone_unique_id)},
            name=display_name,
            manufacturer="Cloud Electronics",
            model="DCM1 Zone Mixer Zone",
        )

    @property
    def is_on(self) -> bool:
        return self._paging_flags.get(self._zone_id, True)

    async def async_turn_on(self, **kwargs) -> None:
        self._paging_flags[self._zone_id] = True
        self.async_write_ha_state()
        async_dispatcher_send(self.hass, SIGNAL_PAGING_FLAGS_CHANGED.format(self._entry_id))

    async def async_turn_off(self, **kwargs) -> None:
        self._paging_flags[self._zone_id] = False
        self.async_write_ha_state()
        async_dispatcher_send(self.hass, SIGNAL_PAGING_FLAGS_CHANGED.format(self._entry_id))
