"""Platform for number integration (EQ controls)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydcm1.mixer import DCM1Mixer

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ENTITY_NAME_SUFFIX, CONF_USE_ZONE_LABELS, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add number entities for EQ controls."""
    mixer: DCM1Mixer = hass.data[DOMAIN][config_entry.entry_id]
    name = config_entry.data[CONF_NAME]
    use_zone_labels = config_entry.data.get(CONF_USE_ZONE_LABELS, True)
    entity_name_suffix = config_entry.data.get(CONF_ENTITY_NAME_SUFFIX, "")

    _LOGGER.debug("Setting up DCM1 EQ number entities for %s", name)

    # Get zone entities from media_player platform so we can register EQ entities with their parent zones
    zone_entities = hass.data[DOMAIN].get("zone_entities", {}).get(config_entry.entry_id, {})
    if not zone_entities:
        # media_player may still be setting up; wait briefly for it to populate
        for _ in range(20):  # up to ~5 seconds
            await asyncio.sleep(0.25)
            zone_entities = hass.data[DOMAIN].get("zone_entities", {}).get(config_entry.entry_id, {})
            if zone_entities:
                break
    if not zone_entities:
        _LOGGER.warning("Zone entities not found - media_player platform not ready")
        return

    entities = []

    # Create EQ entities for each zone and register them with their parent zone entity
    for zone_id, zone in mixer.zones_by_id.items():
        zone_entity = zone_entities.get(zone_id)
        if not zone_entity:
            _LOGGER.warning("Zone entity %s not found, skipping EQ entities", zone_id)
            continue
            
        for parameter in ["treble", "mid", "bass"]:
            entity = DCM1ZoneEQ(
                zone_id=zone_id,
                zone_name=zone.name,
                parameter=parameter,
                mixer=mixer,
                config_entry_id=config_entry.entry_id,
                device_name=name,
                use_zone_labels=use_zone_labels,
                entity_name_suffix=entity_name_suffix,
            )
            # Register entity with its parent zone (zone owns its EQ entities)
            zone_entity.register_eq_entity(parameter, entity)
            entities.append(entity)

    _LOGGER.info("Adding %s EQ number entities", len(entities))
    async_add_entities(entities)


class DCM1ZoneEQ(NumberEntity):
    """Number entity for zone EQ control (treble, mid, or bass)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = -14
    _attr_native_max_value = 14
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "dB"

    def __init__(
        self,
        zone_id: int,
        zone_name: str,
        parameter: str,
        mixer: DCM1Mixer,
        config_entry_id: str,
        device_name: str,
        use_zone_labels: bool = True,
        entity_name_suffix: str = "",
    ) -> None:
        """Initialize the EQ number entity."""
        self.zone_id = zone_id
        self.zone_name = zone_name
        self.parameter = parameter  # "treble", "mid", or "bass"
        self._mixer = mixer
        self._config_entry_id = config_entry_id
        self._device_name = device_name
        self._use_zone_labels = use_zone_labels
        self._entity_name_suffix = entity_name_suffix

        # Set entity attributes
        self._attr_unique_id = f"{config_entry_id}_zone_{zone_id}_eq_{parameter}"
        display_zone_name = zone_name if self._use_zone_labels else f"Zone {zone_id}"
        if self._entity_name_suffix:
            display_zone_name = f"{display_zone_name} {self._entity_name_suffix}"
        self._attr_name = f"{display_zone_name} EQ {parameter.capitalize()}"
        
        # Set icon based on parameter
        icon_map = {
            "treble": "mdi:sine-wave",
            "mid": "mdi:equalizer",
            "bass": "mdi:waveform",
        }
        self._attr_icon = icon_map.get(parameter, "mdi:equalizer-outline")

        # Get initial value from mixer
        zone = mixer.zones_by_id.get(zone_id)
        if zone:
            if parameter == "treble":
                self._attr_native_value = zone.eq_treble
            elif parameter == "mid":
                self._attr_native_value = zone.eq_mid
            elif parameter == "bass":
                self._attr_native_value = zone.eq_bass

        _LOGGER.debug(
            "Created EQ %s entity for zone %s: %s (initial value: %s)",
            parameter,
            zone_id,
            self._attr_unique_id,
            self._attr_native_value,
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info to link this entity to the zone's media_player."""
        display_name = self.zone_name if self._use_zone_labels else f"Zone {self.zone_id}"
        if self._entity_name_suffix:
            display_name = f"{display_name} {self._entity_name_suffix}"
        
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._config_entry_id}_zone_{self.zone_id}")},
            name=display_name,
            manufacturer="Cloud Electronics",
            model="DCM1 Zone",
            via_device=(DOMAIN, self._config_entry_id),
        )

    async def async_set_native_value(self, value: float) -> None:
        """Set the EQ value."""
        int_value = int(value)
        _LOGGER.info(
            "Setting zone %s EQ %s to %+d dB",
            self.zone_id,
            self.parameter,
            int_value,
        )

        # Call the appropriate mixer method based on parameter
        if self.parameter == "treble":
            self._mixer.set_zone_eq_treble(self.zone_id, int_value)
        elif self.parameter == "mid":
            self._mixer.set_zone_eq_mid(self.zone_id, int_value)
        elif self.parameter == "bass":
            self._mixer.set_zone_eq_bass(self.zone_id, int_value)

        # Optimistically update the value (will be confirmed by device callback)
        self._attr_native_value = int_value
        self.async_write_ha_state()

    def update_value(self, value: int) -> None:
        """Update the value from device callback."""
        if self._attr_native_value != value:
            _LOGGER.debug(
                "Zone %s EQ %s updated from device: %+d dB",
                self.zone_id,
                self.parameter,
                value,
            )
            self._attr_native_value = value
            self.async_write_ha_state()

    def set_available(self, available: bool) -> None:
        """Set availability for this EQ entity."""
        self._attr_available = available
        self.async_write_ha_state()
