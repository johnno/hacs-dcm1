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

from .const import (
    CONF_ENTITY_NAME_SUFFIX,
    CONF_OPTIMISTIC_VOLUME,
    CONF_USE_ZONE_LABELS,
    DOMAIN,
)

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
    use_optimistic_volume = config_entry.data.get(CONF_OPTIMISTIC_VOLUME, True)
    volume_db_range = config_entry.data.get("volume_db_range", 40)  # dB range for slider (40 = practical, 61 = full)
    
    # Query zone and source labels BEFORE creating entities
    _LOGGER.info("Querying zone labels, source labels, and volume levels")
    mixer.query_all_labels()
    
    # Wait for label queries to complete (with 10s timeout)
    if hasattr(mixer, 'wait_for_zone_source_labels'):
        _LOGGER.info("Waiting for zone/source labels...")
        labels_loaded = await mixer.wait_for_zone_source_labels(timeout=10.0)
        if not labels_loaded:
            _LOGGER.warning("Timeout waiting for zone/source labels - some names may not be correct")
        else:
            _LOGGER.info("Zone/source labels loaded successfully")
    else:
        _LOGGER.warning("wait_for_zone_source_labels not available - using sleep fallback")
        await asyncio.sleep(3.0)
    
    # Query group information (labels, status, volume, and line inputs)
    _LOGGER.info("Querying group information")
    mixer.query_all_groups()
    # Wait for group data to be received (with 10s timeout)
    if hasattr(mixer, 'wait_for_group_data'):
        _LOGGER.info("Waiting for group data...")
        groups_loaded = await mixer.wait_for_group_data(timeout=10.0)
        if not groups_loaded:
            _LOGGER.warning("Timeout waiting for group data - some groups may not be available")
        else:
            _LOGGER.info("Group data loaded successfully")
    else:
        _LOGGER.warning("wait_for_group_data not available - using sleep fallback")
        await asyncio.sleep(5.0)
    
    # Query line input enables for all zones BEFORE creating entities
    _LOGGER.info("Querying line input enables for all zones")
    for zone_id in mixer.zones_by_id.keys():
        mixer.query_line_inputs(zone_id)
    
    # Wait for line input queries to complete (with 10s timeout)
    if hasattr(mixer, 'wait_for_zone_line_inputs'):
        _LOGGER.info("Waiting for zone line inputs...")
        line_inputs_loaded = await mixer.wait_for_zone_line_inputs(timeout=10.0)
        if not line_inputs_loaded:
            _LOGGER.warning("Timeout waiting for zone line inputs - some zones may have incomplete source lists")
        else:
            _LOGGER.info("Zone line inputs loaded successfully")
    else:
        _LOGGER.warning("wait_for_zone_line_inputs not available - using sleep fallback")
        await asyncio.sleep(7.0)
    
    my_listener = MyListener()
    mixer.register_listener(my_listener)
    entities = []

    # Setup the individual zone entities
    _LOGGER.info("DEBUG: _zone_line_inputs_map contents: %s", mixer.protocol._zone_line_inputs_map)
    for zone_id, zone in mixer.zones_by_id.items():
        _LOGGER.debug("Setting up zone entity for zone_id: %s, %s", zone.id, zone.name)
        # Get enabled line inputs for this zone
        enabled_inputs = mixer.get_enabled_line_inputs(zone_id)
        _LOGGER.info("DEBUG: Zone %s enabled_inputs returned: %s", zone_id, enabled_inputs)
        _LOGGER.info("DEBUG: Zone %s type: %s, bool: %s, len: %s", zone_id, type(enabled_inputs), bool(enabled_inputs), len(enabled_inputs) if enabled_inputs else 0)
        mixer_zone = MixerZone(zone.id, zone.name, mixer, use_zone_labels, entity_name_suffix, enabled_inputs, use_optimistic_volume, volume_db_range)
        my_listener.add_mixer_zone_entity(zone.id, mixer_zone)
        entities.append(mixer_zone)

    # Setup entities for enabled groups only
    _LOGGER.info("Checking groups for entity creation: %s groups found", len(mixer.groups_by_id))
    _LOGGER.info("DEBUG: _group_line_inputs_map contents: %s", mixer.protocol._group_line_inputs_map)
    for group_id, group in mixer.groups_by_id.items():
        _LOGGER.info("Group %s: name='%s', enabled=%s, zones=%s", group.id, group.name, group.enabled, group.zones)
        if group.enabled:
            _LOGGER.info("Creating group entity for group_id: %s, %s (ENABLED)", group.id, group.name)
            # Get enabled line inputs for this group
            enabled_inputs = mixer.protocol.get_enabled_group_line_inputs(group_id)
            _LOGGER.info("DEBUG: Group %s enabled_inputs returned: %s", group_id, enabled_inputs)
            _LOGGER.info("DEBUG: Type of enabled_inputs: %s, bool check: %s", type(enabled_inputs), bool(enabled_inputs))
            mixer_group = MixerGroup(group.id, group.name, mixer, use_zone_labels, entity_name_suffix, enabled_inputs, use_optimistic_volume, volume_db_range)
            my_listener.add_mixer_group_entity(group.id, mixer_group)
            entities.append(mixer_group)
        else:
            _LOGGER.info("Skipping DISABLED group: group_id: %s, %s", group.id, group.name)

    # Request a status update so all listeners are notified with current status
    _LOGGER.info("Refreshing status after setup")
    _LOGGER.info("Total entities to add: %s", len(entities))
    mixer.update_status()
    
    async_add_entities(entities)


class MyListener(SourceChangeListener):
    """Listener to direct messages to correct entities."""

    def __init__(self) -> None:
        """Init."""
        self.mixer_zone_entities: dict[int, MixerZone] = {}
        self.mixer_group_entities: dict[int, MixerGroup] = {}

    def add_mixer_zone_entity(self, zone_id, entity):
        """Add a Mixer Zone Entity."""
        self.mixer_zone_entities[zone_id] = entity

    def add_mixer_group_entity(self, group_id, entity):
        """Add a Mixer Group Entity."""
        self.mixer_group_entities[group_id] = entity

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
        # Update all group entities with new source list
        for entity in self.mixer_group_entities.values():
            entity.update_source_list()
        # Update all group entities with new source list
        for entity in self.mixer_group_entities.values():
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

    def group_label_changed(self, group_id: int, label: str):
        """Group label changed callback."""
        _LOGGER.debug("Group label changed for Group ID %s to: %s", group_id, label)
        entity = self.mixer_group_entities.get(group_id)
        if entity:
            entity.set_name(label)

    def group_status_changed(self, group_id: int, enabled: bool, zones: list[int]):
        """Group status changed callback."""
        _LOGGER.debug("Group status changed for Group ID %s: enabled=%s, zones=%s", group_id, enabled, zones)
        # Note: If group is disabled at runtime, the entity will remain but won't receive updates

    def group_line_inputs_changed(self, group_id: int, enabled_inputs: dict[int, bool]):
        """Group line inputs enabled status changed callback."""
        _LOGGER.debug("Group line inputs changed for Group ID %s: %s", group_id, enabled_inputs)
        entity = self.mixer_group_entities.get(group_id)
        if entity:
            entity.update_enabled_inputs(enabled_inputs)

    def group_volume_changed(self, group_id: int, level):
        """Group volume level changed callback."""
        _LOGGER.debug("Group volume level changed for Group ID %s to: %s", group_id, level)
        entity = self.mixer_group_entities.get(group_id)
        if entity:
            entity.set_volume(level)

    def group_source_changed(self, group_id: int, source_id: int):
        """Group source changed callback."""
        _LOGGER.debug("Group source changed for Group ID %s to source ID: %s", group_id, source_id)
        entity = self.mixer_group_entities.get(group_id)
        if entity:
            entity.set_source(source_id)

    def connected(self):
        """Mixer connected callback. No action as status will be updated."""

    def disconnected(self):
        """Mixer disconnected callback."""
        _LOGGER.warning("DCM1 Mixer disconnected")
        for entity in self.mixer_zone_entities.values():
            _LOGGER.debug("Updating zone %s", entity)
            entity.set_state(MediaPlayerState.UNAVAILABLE)
        for entity in self.mixer_group_entities.values():
            _LOGGER.debug("Updating group %s", entity)
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

    def __init__(self, zone_id, zone_name, mixer, use_zone_labels=True, entity_name_suffix="", enabled_line_inputs=None, use_optimistic_volume=True, volume_db_range=40) -> None:
        """Init."""
        self.zone_id = zone_id
        self._mixer: DCM1Mixer = mixer
        self._use_zone_labels = use_zone_labels
        self._entity_name_suffix = entity_name_suffix
        self._enabled_line_inputs: dict[int, bool] = enabled_line_inputs or {}
        self._use_optimistic_volume = use_optimistic_volume
        self._volume_db_range = max(1, min(61, volume_db_range))  # Clamp to valid range
        
        _LOGGER.debug(f"Zone {zone_id} enabled_line_inputs: {self._enabled_line_inputs}")
        
        self._attr_source_list = self._build_source_list()
        self._attr_state = MediaPlayerState.ON
        self._volume_level = None  # Confirmed volume from device
        self._pending_volume = None  # User's uncommitted volume request
        self._is_volume_muted = False
        self._raw_volume_level = None  # Last raw device volume level (0-62)
        
        # Try to get initial source state
        initial_source_id = mixer.status_of_zone(zone_id)
        if initial_source_id and initial_source_id in mixer.sources_by_id:
            self._attr_source = mixer.sources_by_id[initial_source_id].name
        
        # Try to get initial volume level
        initial_volume = mixer.protocol.get_volume_level(zone_id)
        if initial_volume is not None:
            level_int = 62 if initial_volume == "mute" else int(initial_volume)
            self._raw_volume_level = level_int
            if level_int >= 62:
                self._is_volume_muted = True
                self._attr_is_volume_muted = True
                self._volume_level = 0.0
            else:
                self._is_volume_muted = False
                self._attr_is_volume_muted = False
                # Convert DCM1 level to HA volume (0.0-1.0)
                # Linear mapping in dB space (dB is already logarithmic, matches human perception)
                # Configurable range: 0% → -volume_db_range dB, 100% → 0 dB
                # Example: range=40 means 0%=-40dB, 50%=-20dB, 100%=0dB
                if level_int >= self._volume_db_range:
                    self._volume_level = 0.0  # Below usable range
                else:
                    # Linear: volume = 1 - (level / range)
                    self._volume_level = 1.0 - (level_int / self._volume_db_range)
            self._attr_volume_level = self._volume_level

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
            _LOGGER.warning(f"Zone {self.zone_id}: No line input data, showing all sources")
            return [s.name for s in self._mixer.sources_by_id.values()]
        
        _LOGGER.debug(f"Zone {self.zone_id}: Filtering sources with enabled inputs: {self._enabled_line_inputs}")
        
        # Filter to only show sources whose line input is enabled
        filtered_sources = []
        for source_id, source in self._mixer.sources_by_id.items():
            # Only filter sources 1-8 (line inputs), allow any other sources
            if 1 <= source_id <= 8:
                if self._enabled_line_inputs.get(source_id, False):
                    filtered_sources.append(source.name)
                    _LOGGER.debug(f"Zone {self.zone_id}: Including source {source_id} ({source.name})")
                else:
                    _LOGGER.debug(f"Zone {self.zone_id}: Excluding source {source_id} ({source.name})")
            else:
                filtered_sources.append(source.name)
        
        _LOGGER.info(f"Zone {self.zone_id}: Final source list: {filtered_sources}")
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
            self._raw_volume_level = 62
        else:
            self._is_volume_muted = False
            self._attr_is_volume_muted = False
            # Convert DCM1 level to HA volume (0.0-1.0)
            # Linear mapping in dB space: volume = 1 - (level / range)
            level_int = int(level)
            self._raw_volume_level = level_int
            if level_int >= self._volume_db_range:
                new_volume = 0.0  # Below usable range maps to 0%
            else:
                new_volume = 1.0 - (level_int / self._volume_db_range)
            
            # Check if this confirms a pending user request or is an external change
            # If we have a pending volume, check if device level matches what user requested
            if self._pending_volume is not None:
                if self._pending_volume == 0.0:
                    expected_level = self._volume_db_range  # 0% maps to max attenuation
                else:
                    expected_level = round(self._volume_db_range * (1.0 - self._pending_volume))
                
                if expected_level == level_int:
                    # Device confirmed user's request - commit pending to confirmed
                    self._volume_level = self._pending_volume
                    self._pending_volume = None
                    self._attr_volume_level = self._volume_level
                else:
                    # Device reports different level - external change (physical knob)
                    # Override pending with actual device state
                    self._volume_level = new_volume
                    self._pending_volume = None
                    self._attr_volume_level = new_volume
            else:
                # No pending request - this is either initial state or external change
                # No pending request - check if current position already produces this level
                # (hysteresis: multiple HA volumes can round to same device level)
                if self._volume_level is not None:
                    current_would_be = self._volume_db_range if self._volume_level == 0.0 else round(self._volume_db_range * (1 - self._volume_level))
                    if current_would_be == level_int:
                        # Current slider position already produces this level - keep it
                        self._attr_volume_level = self._volume_level
                    else:
                        # Different level - update to device's value
                        self._volume_level = new_volume
                        self._attr_volume_level = new_volume
                else:
                    # No current volume - set to device value
                    self._volume_level = new_volume
                    self._attr_volume_level = new_volume
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
        # Convert HA volume (0.0-1.0) to DCM1 level
        # Linear mapping in dB space: level = range * (1 - volume)
        # Example with range=40: 0%→40 (-40dB), 50%→20 (-20dB), 100%→0 (0dB)
        # HA 0.0 = max attenuation, HA 1.0 = no attenuation (0 dB)
        if volume == 0.0:
            level = self._volume_db_range  # 0% maps to minimum volume
        else:
            level = round(self._volume_db_range * (1.0 - volume))
            level = max(0, min(self._volume_db_range, level))  # Clamp to valid range
        
        # Store user's request as pending (uncommitted)
        # If optimistic UI enabled, show immediately; otherwise wait for confirmation
        self._pending_volume = volume
        if self._use_optimistic_volume:
            self._attr_volume_level = volume  # UI shows pending state
            self.schedule_update_ha_state()  # Update UI immediately
        
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

    @property
    def extra_state_attributes(self):
        """Return integration-specific debugging attributes."""
        attrs = {}
        if self._raw_volume_level is not None:
            attrs["dcm1_raw_volume_level"] = self._raw_volume_level
        if self._pending_volume is not None:
            attrs["dcm1_pending_volume"] = round(self._pending_volume, 4)
        if self._volume_level is not None:
            attrs["dcm1_confirmed_volume"] = round(self._volume_level, 4)
        return attrs

    def mute_volume(self, mute: bool) -> None:
        """Mute or unmute the volume."""
        if mute:
            self._mixer.set_volume(zone_id=self.zone_id, level=62)  # 62 = mute
        else:
            # Unmute to last known level, or default to mid-range
            if self._volume_level is not None and self._volume_level > 0.0:
                # Linear: level = range * (1 - volume)
                level = round(self._volume_db_range * (1.0 - self._volume_level))
            else:
                level = self._volume_db_range // 2  # Default to mid-range if slider at 0% or unknown
            self._mixer.set_volume(zone_id=self.zone_id, level=level)
class MixerGroup(MediaPlayerEntity):
    """Represents an enabled Group of the DCM1 Mixer."""

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

    def __init__(self, group_id, group_name, mixer, use_zone_labels=True, entity_name_suffix="", enabled_line_inputs=None, use_optimistic_volume=True, volume_db_range=40) -> None:
        """Init."""
        self.group_id = group_id
        self._mixer: DCM1Mixer = mixer
        self._use_zone_labels = use_zone_labels
        self._entity_name_suffix = entity_name_suffix
        self._enabled_line_inputs: dict[int, bool] = enabled_line_inputs or {}
        self._use_optimistic_volume = use_optimistic_volume
        self._volume_db_range = max(1, min(61, volume_db_range))  # Clamp to valid range
        
        _LOGGER.debug(f"Group {group_id} enabled_line_inputs: {self._enabled_line_inputs}")
        
        self._attr_source_list = self._build_source_list()
        self._attr_state = MediaPlayerState.ON
        self._volume_level = None  # Confirmed volume from device
        self._pending_volume = None  # User's uncommitted volume request
        self._is_volume_muted = False
        self._attr_is_volume_muted = False
        self._attr_volume_level = None
        self._raw_volume_level = None  # Last raw device volume level (0-62)
        
        # Try to get initial source state
        initial_source_id = mixer.protocol.get_group_source(group_id)
        if initial_source_id and initial_source_id in mixer.sources_by_id:
            self._attr_source = mixer.sources_by_id[initial_source_id].name
            _LOGGER.info(f"Group {group_id} initial source: {initial_source_id} ({self._attr_source})")
        else:
            _LOGGER.warning(f"Group {group_id} initial source is None or invalid: {initial_source_id}")
        
        # Try to get initial volume level
        initial_volume = mixer.protocol.get_group_volume_level(group_id)
        _LOGGER.info(f"Group {group_id} initial volume from protocol: {initial_volume}")
        if initial_volume is not None:
            level_int = 62 if initial_volume == "mute" else int(initial_volume)
            self._raw_volume_level = level_int
            if level_int >= 62:
                self._is_volume_muted = True
                self._attr_is_volume_muted = True
                self._volume_level = 0.0
                _LOGGER.info(f"Group {group_id} is muted")
            else:
                self._is_volume_muted = False
                self._attr_is_volume_muted = False
                # Convert DCM1 level to HA volume (0.0-1.0)
                # Linear mapping in dB space (dB is already logarithmic, matches human perception)
                # Configurable range: 0% → -volume_db_range dB, 100% → 0 dB
                if level_int >= self._volume_db_range:
                    self._volume_level = 0.0  # Below usable range
                else:
                    self._volume_level = 1.0 - (level_int / self._volume_db_range)
                self._attr_volume_level = self._volume_level
                _LOGGER.info(f"Group {group_id} volume set to {self._attr_volume_level} (level {initial_volume})")
        else:
            _LOGGER.warning(f"Group {group_id} initial volume is None - volume data not loaded yet")

        # Use hostname as unique identifier since DCM1 doesn't have a MAC
        unique_base = f"dcm1_{self._mixer.hostname.replace('.', '_')}"
        self._attr_unique_id = f"{unique_base}_group{group_id}"

        # Build display name based on configuration
        if use_zone_labels:
            display_name = group_name
        else:
            display_name = f"Group {group_id}"
        
        if entity_name_suffix:
            display_name = f"{display_name} {entity_name_suffix}"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, self._attr_unique_id)},
            "name": display_name,
            "manufacturer": "Cloud Electronics",
            "model": "DCM1 Zone Mixer Group",
        }

    def set_state(self, state):
        """Set the state."""
        self._attr_state = state
        self.schedule_update_ha_state()

    def set_name(self, name: str):
        """Set the group name."""
        if self._attr_device_info:
            if self._use_zone_labels:
                display_name = name
            else:
                display_name = f"Group {self.group_id}"
            
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
            _LOGGER.warning(f"Group {self.group_id}: No line input data, showing all sources")
            return [s.name for s in self._mixer.sources_by_id.values()]
        
        _LOGGER.debug(f"Group {self.group_id}: Filtering sources with enabled inputs: {self._enabled_line_inputs}")
        
        # Filter to only show sources whose line input is enabled
        filtered_sources = []
        for source_id, source in self._mixer.sources_by_id.items():
            # Only filter sources 1-8 (line inputs), allow any other sources
            if 1 <= source_id <= 8:
                if self._enabled_line_inputs.get(source_id, False):
                    filtered_sources.append(source.name)
                    _LOGGER.debug(f"Group {self.group_id}: Including source {source_id} ({source.name})")
                else:
                    _LOGGER.debug(f"Group {self.group_id}: Excluding source {source_id} ({source.name})")
            else:
                filtered_sources.append(source.name)
        
        _LOGGER.info(f"Group {self.group_id}: Final source list: {filtered_sources}")
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
            self._raw_volume_level = 62
        else:
            self._is_volume_muted = False
            self._attr_is_volume_muted = False
            # Convert DCM1 level to HA volume (0.0-1.0)
            # Linear mapping in dB space: volume = 1 - (level / range)
            level_int = int(level)
            self._raw_volume_level = level_int
            if level_int >= self._volume_db_range:
                new_volume = 0.0  # Below usable range maps to 0%
            else:
                new_volume = 1.0 - (level_int / self._volume_db_range)
            
            # Pending/committed pattern: check if this confirmation matches user's pending request
            # If it matches → commit the pending value (user got what they wanted)
            # If it doesn't match → external change (physical knob), override pending with actual
            # If no pending → regular state update (heartbeat polling)
            if self._pending_volume is not None:
                # We have a pending user request - check if device confirmed it
                if self._pending_volume == 0.0:
                    expected_level = self._volume_db_range  # 0% maps to max attenuation
                else:
                    expected_level = round(self._volume_db_range * (1.0 - self._pending_volume))
                
                if expected_level == level_int:
                    # Device confirmed our pending request - commit it
                    self._volume_level = self._pending_volume
                    self._pending_volume = None
                    self._attr_volume_level = self._volume_level
                else:
                    # Device reports different level - external change (physical control)
                    # Override pending with actual device state
                    self._volume_level = new_volume
                    self._pending_volume = None
                    self._attr_volume_level = new_volume
            else:
                # No pending request - regular state update
                # No pending request - check if current position already produces this level
                # (hysteresis: multiple HA volumes can round to same device level)
                if self._volume_level is not None:
                    current_would_be = self._volume_db_range if self._volume_level == 0.0 else round(self._volume_db_range * (1 - self._volume_level))
                    if current_would_be == level_int:
                        # Current slider position already produces this level - keep it
                        self._attr_volume_level = self._volume_level
                    else:
                        # Different level - update to device's value
                        self._volume_level = new_volume
                        self._attr_volume_level = new_volume
                else:
                    # No current volume - set to device value
                    self._volume_level = new_volume
                    self._attr_volume_level = new_volume
        self.schedule_update_ha_state()

    def select_source(self, source: str) -> None:
        """Select the source."""
        # Find source by name
        source_obj = self._mixer.sources_by_name.get(source)
        if source_obj:
            self._mixer.set_group_source(group_id=self.group_id, source_id=source_obj.id)
        else:
            _LOGGER.error(
                "Invalid source: %s, valid sources %s", source, self._attr_source_list
            )

    def set_volume_level(self, volume: float) -> None:
        """Set volume level (0.0 to 1.0)."""
        # Convert HA volume (0.0-1.0) to DCM1 level
        # Linear mapping in dB space: level = range * (1 - volume)
        # Example with range=40: 0%→40 (-40dB), 50%→20 (-20dB), 100%→0 (0dB)
        # HA 0.0 = max attenuation, HA 1.0 = no attenuation (0 dB)
        if volume == 0.0:
            level = self._volume_db_range  # 0% maps to minimum volume
        else:
            level = round(self._volume_db_range * (1.0 - volume))
            level = max(0, min(self._volume_db_range, level))  # Clamp to valid range
        
        # Store user's request as pending (uncommitted)
        # If optimistic UI enabled, show immediately; otherwise wait for confirmation
        self._pending_volume = volume
        if self._use_optimistic_volume:
            self._attr_volume_level = volume  # UI shows pending state
            self.schedule_update_ha_state()  # Update UI immediately
        
        self._mixer.set_group_volume(group_id=self.group_id, level=level)

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
            self._mixer.set_group_volume(group_id=self.group_id, level=62)  # 62 = mute
        else:
            # Unmute to last known level, or default to mid-range
            if self._volume_level is not None and self._volume_level > 0.0:
                # Linear: level = range * (1 - volume)
                level = round(self._volume_db_range * (1.0 - self._volume_level))
            else:
                level = self._volume_db_range // 2  # Default to mid-range if slider at 0% or unknown
            self._mixer.set_group_volume(group_id=self.group_id, level=level)
