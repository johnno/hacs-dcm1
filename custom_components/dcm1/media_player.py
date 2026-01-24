"""Platform for media_player integration."""

from __future__ import annotations

import asyncio
import logging

from pydcm1.listener import SourceChangeListener
from pydcm1.mixer import DCM1Mixer

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ENTITY_NAME_SUFFIX, CONF_USE_ZONE_LABELS, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add media_player for passed config_entry in HA."""
    mixer: DCM1Mixer = hass.data[DOMAIN][config_entry.entry_id]

    name = config_entry.data[CONF_NAME]

    _LOGGER.debug("Setting up DCM1 entities for %s", name)

    use_zone_labels = config_entry.data.get(CONF_USE_ZONE_LABELS, True)
    entity_name_suffix = config_entry.data.get(CONF_ENTITY_NAME_SUFFIX, "")
    
    # Query zone and source labels BEFORE creating entities
    _LOGGER.info("Querying zone labels, source labels, and volume levels")
    mixer.query_all_labels()
    
    # Wait for label queries to complete (8 zones + 8 sources + 8 volumes = 24 queries * 0.1s = 2.4s)
    # Add extra buffer for network latency
    await asyncio.sleep(3.0)
    
    my_listener = MyListener()
    mixer.register_listener(my_listener)
    zones = []

    # Setup the individual zone entities
    for zone_id, zone in mixer.zones_by_id.items():
        _LOGGER.debug("Setting up zone entity for zone_id: %s, %s", zone.id, zone.name)
        mixer_zone = MixerZone(zone.id, zone.name, mixer, use_zone_labels, entity_name_suffix)
        my_listener.add_mixer_zone_entity(zone.id, mixer_zone)
        zones.append(mixer_zone)

    # Request a status update so all listeners are notified with current status
    _LOGGER.info("Refreshing status after setup")
    _LOGGER.info("Total entities to add: %s", len(zones))
    mixer.update_status()
    
    # Query line input enables for all zones to filter source lists
    for zone_id in mixer.zones_by_id.keys():
        mixer.query_line_inputs(zone_id)
    
    async_add_entities(zones)


class MyListener(SourceChangeListener):
    """Listener to direct messages to correct entities."""

    def __init__(self) -> None:
        """Init."""
        self.mixer_zone_entities: dict[int, MixerZone] = {}

    def add_mixer_zone_entity(self, zone_id, entity):
        """Add a Mixer Zone Entity."""
        self.mixer_zone_entities[zone_id] = entity

    def source_changed(self, zone_id: int, source_id: int):
        """Source changed callback."""
        _LOGGER.debug(
            "Source changed Zone ID %s changed to source ID: %s", zone_id, source_id
        )
        entity = self.mixer_zone_entities.get(zone_id)
        if entity:
            _LOGGER.debug("Updating entity for source changed")
            entity.set_source(source_id)

    def zone_label_changed(self, zone_id: int, label: str):
        """Zone label changed callback."""
        _LOGGER.debug("Zone label changed for Zone ID %s to: %s", zone_id, label)
        entity = self.mixer_zone_entities.get(zone_id)
        if entity:
            entity.set_name(label)

    def source_label_changed(self, source_id: int, label: str):
        """Source label changed callback."""
        _LOGGER.debug("Source label changed for Source ID %s to: %s", source_id, label)
        # Update all zone entities with new source list
        for entity in self.mixer_zone_entities.values():
            entity.update_source_list()

    def line_inputs_changed(self, zone_id: int, enabled_inputs: dict[int, bool]):
        """Line inputs enabled status changed callback."""
        _LOGGER.debug("Line inputs changed for Zone ID %s: %s", zone_id, enabled_inputs)
        entity = self.mixer_zone_entities.get(zone_id)
        if entity:
            entity.update_enabled_inputs(enabled_inputs)

    def volume_level_changed(self, zone_id: int, level):
        """Volume level changed callback."""
        _LOGGER.debug("Volume level changed for Zone ID %s to: %s", zone_id, level)
        entity = self.mixer_zone_entities.get(zone_id)
        if entity:
            entity.set_volume(level)

    def connected(self):
        """Mixer connected callback. No action as status will be updated."""

    def disconnected(self):
        """Mixer disconnected callback."""
        _LOGGER.warning("DCM1 Mixer disconnected")
        for entity in self.mixer_zone_entities.values():
            _LOGGER.debug("Updating %s", entity)
            entity.set_state(MediaPlayerState.UNAVAILABLE)

    def power_changed(self, power: bool):
        """Power changed callback - DCM1 has physical switch only, no power control."""
        pass

    def error(self, error_message: str):
        """Error callback not required for us."""

    def source_change_requested(self, zone_id: int, source_id: int):
        """Ignore callback from API - not required for us."""


class MixerZone(MediaPlayerEntity):
    """Represents the Zones of the DCM1 Mixer."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = None

    _attr_supported_features = (
        MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
    )
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER

    def __init__(self, zone_id, zone_name, mixer, use_zone_labels=True, entity_name_suffix="") -> None:
        """Init."""
        self.zone_id = zone_id
        self._mixer: DCM1Mixer = mixer
        self._use_zone_labels = use_zone_labels
        self._entity_name_suffix = entity_name_suffix
        self._enabled_line_inputs: dict[int, bool] = {}
        self._attr_source_list = self._build_source_list()
        self._attr_state = MediaPlayerState.ON
        self._volume_level = None
        self._is_volume_muted = False
        
        # Try to get initial source state
        initial_source_id = mixer.status_of_zone(zone_id)
        if initial_source_id and initial_source_id in mixer.sources_by_id:
            self._attr_source = mixer.sources_by_id[initial_source_id].name

        # Use hostname as unique identifier since DCM1 doesn't have a MAC
        unique_base = f"dcm1_{self._mixer.hostname.replace('.', '_')}"
        self._attr_unique_id = f"{unique_base}_zone{zone_id}"

        # Build display name based on configuration
        if use_zone_labels:
            display_name = zone_name
        else:
            display_name = f"Zone {zone_id}"
        
        if entity_name_suffix:
            display_name = f"{display_name} {entity_name_suffix}"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, self._attr_unique_id)},
            "name": display_name,
            "manufacturer": "Cloud Electronics",
            "model": "DCM1 Zone Mixer",
        }

    def set_state(self, state):
        """Set the state."""
        self._attr_state = state
        self.schedule_update_ha_state()

    def set_name(self, name: str):
        """Set the zone name."""
        if self._attr_device_info:
            if self._use_zone_labels:
                display_name = name
            else:
                display_name = f"Zone {self.zone_id}"
            
            if self._entity_name_suffix:
                display_name = f"{display_name} {self._entity_name_suffix}"
            
            self._attr_device_info["name"] = display_name
        self.schedule_update_ha_state()

    def set_source(self, source_id):
        """Set the active source."""
        # Find source by ID
        source = self._mixer.sources_by_id.get(source_id)
        if source:
            self._attr_source = source.name
            self.schedule_update_ha_state()

    def _build_source_list(self) -> list[str]:
        """Build filtered source list based on enabled line inputs."""
        if not self._enabled_line_inputs:
            # If no line input data yet, show all sources
            return [s.name for s in self._mixer.sources_by_id.values()]
        
        # Filter to only show sources whose line input is enabled
        filtered_sources = []
        for source_id, source in self._mixer.sources_by_id.items():
            # Only filter sources 1-8 (line inputs), allow any other sources
            if 1 <= source_id <= 8:
                if self._enabled_line_inputs.get(source_id, False):
                    filtered_sources.append(source.name)
            else:
                filtered_sources.append(source.name)
        
        return filtered_sources

    def update_source_list(self):
        """Update the source list from mixer."""
        self._attr_source_list = self._build_source_list()
        self.schedule_update_ha_state()

    def update_enabled_inputs(self, enabled_inputs: dict[int, bool]):
        """Update the enabled line inputs and refresh source list."""
        self._enabled_line_inputs = enabled_inputs
        self._attr_source_list = self._build_source_list()
        self.schedule_update_ha_state()

    def set_volume(self, level):
        """Set the volume level from the mixer."""
        if level == "mute":
            self._is_volume_muted = True
            self._attr_is_volume_muted = True
        else:
            self._is_volume_muted = False
            self._attr_is_volume_muted = False
            # Convert DCM1 level (0-61) to HA volume (0.0-1.0)
            # Level 0 = 0dB (max), Level 61 = -61dB (min)
            # Invert so 0 = quietest, 61 = loudest
            self._volume_level = (61 - int(level)) / 61.0
            self._attr_volume_level = self._volume_level
        self.schedule_update_ha_state()

    def select_source(self, source: str) -> None:
        """Select the source."""
        # Find source by name
        source_obj = self._mixer.sources_by_name.get(source)
        if source_obj:
            self._mixer.set_zone_source(zone_id=self.zone_id, source_id=source_obj.id)
        else:
            _LOGGER.error(
                "Invalid source: %s, valid sources %s", source, self._attr_source_list
            )

    def set_volume_level(self, volume: float) -> None:
        """Set volume level (0.0 to 1.0)."""
        # Convert HA volume (0.0-1.0) to DCM1 level (0-61)
        # HA 0.0 = quietest = DCM1 61, HA 1.0 = loudest = DCM1 0
        level = int(61 - (volume * 61))
        level = max(0, min(61, level))  # Clamp to valid range
        self._mixer.set_volume(zone_id=self.zone_id, level=level)

    def volume_up(self) -> None:
        """Increase volume by one step."""
        if self._volume_level is not None:
            new_volume = min(1.0, self._volume_level + 0.05)  # 5% increment
            self.set_volume_level(new_volume)

    def volume_down(self) -> None:
        """Decrease volume by one step."""
        if self._volume_level is not None:
            new_volume = max(0.0, self._volume_level - 0.05)  # 5% decrement
            self.set_volume_level(new_volume)

    def mute_volume(self, mute: bool) -> None:
        """Mute or unmute the volume."""
        if mute:
            self._mixer.set_volume(zone_id=self.zone_id, level=62)  # 62 = mute
        else:
            # Unmute to last known level, or default to -20dB (level 20)
            if self._volume_level is not None:
                level = int(61 - (self._volume_level * 61))
            else:
                level = 20  # Default to -20dB
            self._mixer.set_volume(zone_id=self.zone_id, level=level)
