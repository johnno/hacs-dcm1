"""Platform for media_player integration."""

from __future__ import annotations

import asyncio
import asyncio.subprocess
import hashlib
import logging
import os
import shutil

from pydcm1.listener import MixerResponseListener
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
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.network import get_url

from homeassistant.components.media_source import (
    async_resolve_media,
    is_media_source_id,
)

from .const import (
    CONF_ENTITY_NAME_SUFFIX,
    CONF_OPTIMISTIC_VOLUME,
    CONF_PAGING_POST_DELAY_MS,
    CONF_PAGING_PRE_DELAY_MS,
    CONF_PAGING_USB_DEVICE,
    CONF_USE_ZONE_LABELS,
    CONF_VOLUME_DB_RANGE,
    DEFAULT_PAGING_POST_DELAY_MS,
    DEFAULT_PAGING_PRE_DELAY_MS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Default paging timing (can be overridden via config_entry.data)
_PAGING_PRE_DELAY_MS_DEFAULT = 500   # ms after paging open, before audio plays
_PAGING_POST_DELAY_MS_DEFAULT = 200  # ms after audio ends, before paging closes


async def _play_paging_audio(
    media_id: str,
    usb_device: str | None,
    logger,
) -> None:
    """Play audio through the USB DI sound device for paging.

    Attempts to use ffplay (Linux / HA OS) then afplay (macOS) in order.
    If those are missing, falls back to ffmpeg directly (which is more
    common in HA environments).

    Args:
        media_id: Local file path or HTTP URL to the audio file.
        usb_device: Optional ALSA/CoreAudio device name. None = system default.
        logger: Logger instance for this call.
    """
    # 1. Try ffplay (preferred as it handles audio output gracefully)
    ffplay_path = shutil.which("ffplay") or shutil.which("/opt/homebrew/bin/ffplay") or shutil.which("/usr/bin/ffplay")
    if ffplay_path:
        cmd = [ffplay_path, "-nodisp", "-autoexit", "-loglevel", "warning"]
        if usb_device:
            cmd += ["-device", usb_device]
        cmd.append(media_id)
        return await _run_audio_cmd(cmd, logger)

    # 2. Try afplay (macOS specific)
    afplay_path = shutil.which("afplay") or shutil.which("/usr/bin/afplay")
    if afplay_path:
        cmd = [afplay_path]
        if usb_device:
            cmd += ["-d", usb_device]
        cmd.append(media_id)
        return await _run_audio_cmd(cmd, logger)

    # 3. Fallback to ffmpeg (most common in HA OS / Core via 'ffmpeg:' integration)
    ffmpeg_path = shutil.which("ffmpeg") or shutil.which("/opt/homebrew/bin/ffmpeg") or shutil.which("/usr/bin/ffmpeg")
    if ffmpeg_path:
        # We must specify the output format based on the platform.
        # Darwin = CoreAudio, Linux/HAOS = PulseAudio (default) or ALSA (explicit hw:).
        import platform
        cmd = [ffmpeg_path, "-i", media_id, "-loglevel", "error"]
        if platform.system() == "Darwin":
            cmd += ["-f", "coreaudio", usb_device or "default"]
        else:
            # For Linux/HAOS: 
            # If user specifies 'hw:X,Y', they want direct ALSA hardware access.
            if usb_device and usb_device.startswith("hw:"):
                cmd += ["-f", "alsa", usb_device]
            else:
                # Default to PulseAudio (the 'Proper Way' for HAOS and its audio bridge).
                cmd += ["-f", "pulse", usb_device or "default"]
        return await _run_audio_cmd(cmd, logger)

    logger.error(
        "No suitable audio player found (tried ffplay, afplay, ffmpeg). "
        "Please ensure 'ffmpeg:' is enabled in your HA configuration "
        "or install ffmpeg on your host system."
    )


async def _get_media_duration(hass: HomeAssistant, media_id: str, logger) -> tuple[float | None, str]:
    """Probe media duration, downloading to a temp file if it's a remote URL.
    
    Returns:
        tuple (duration_in_seconds, final_media_path)
    """
    final_media_path = media_id
    
    # Check if we need to download the remote URL
    if media_id.startswith(("http://", "https://")):
        try:
            # Create a deterministic filename in /tmp based on the URL hash
            url_hash = hashlib.md5(media_id.encode()).hexdigest()
            temp_path = f"/tmp/dcm1_paging_{url_hash}.mp3"
            
            # Download the file if it doesn't exist or we want to be fresh
            # (Using a fresh download each time is safer for TTS which might change)
            logger.debug("Downloading remote paging audio: %s", media_id)
            session = async_get_clientsession(hass)
            async with session.get(media_id, timeout=10) as response:
                if response.status == 200:
                    data = await response.read()
                    # Write to file using executor to avoid blocking the event loop
                    def write_file():
                        with open(temp_path, "wb") as f:
                            f.write(data)
                    await hass.async_add_executor_job(write_file)
                    final_media_path = temp_path
                else:
                    logger.warning("Failed to download paging audio, status: %s", response.status)
        except Exception as exc: # noqa: BLE001
            logger.warning("Error downloading paging audio: %s", exc)

    # Now probe the final_media_path (either original local path or new temp path)
    ffprobe_path = (
        shutil.which("ffprobe")
        or shutil.which("/opt/homebrew/bin/ffprobe")
        or shutil.which("/usr/bin/ffprobe")
    )
    
    duration = None
    if ffprobe_path:
        cmd = [
            ffprobe_path,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            final_media_path,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0 and stdout:
                duration_str = stdout.decode().strip()
                try:
                    duration = float(duration_str)
                except ValueError:
                    logger.warning("Invalid duration from ffprobe: %s", duration_str)
            elif stderr:
                logger.debug("ffprobe error: %s", stderr.decode().strip())
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to probe duration with ffprobe: %s", exc)
    else:
        logger.debug("ffprobe not found, cannot probe duration")

    return duration, final_media_path


async def _run_audio_cmd(cmd: list[str], logger) -> None:
    """Helper to execute the process and log errors."""
    logger.info("Paging audio: running %s", " ".join(cmd))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 and stderr:
            logger.warning(
                "Paging audio player exited with code %s: %s",
                proc.returncode,
                stderr.decode(errors="replace").strip(),
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to play paging audio: %s", exc)


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
    volume_db_range = config_entry.data.get(CONF_VOLUME_DB_RANGE, 40)  # dB range for slider (40 = practical, 61 = full)
    paging_pre_delay_ms = config_entry.data.get(CONF_PAGING_PRE_DELAY_MS, DEFAULT_PAGING_PRE_DELAY_MS)
    paging_post_delay_ms = config_entry.data.get(CONF_PAGING_POST_DELAY_MS, DEFAULT_PAGING_POST_DELAY_MS)
    paging_usb_device = config_entry.data.get(CONF_PAGING_USB_DEVICE, None)
    
    # Query all mixer state (zones, sources, groups, line inputs, volume, status)
    #_LOGGER.info("Querying all mixer state from device")
    #mixer.query_status()
    
    # Wait for all data to be received from device
    _LOGGER.info("Waiting for source labels...")
    sources_loaded = await mixer.wait_for_source_labels(timeout=7.0)
    if not sources_loaded:
        _LOGGER.warning("Timeout waiting for source labels - some names may not be correct")
    
    _LOGGER.info("Waiting for zone data (labels, sources, line inputs, volume)...")
    zones_loaded = await mixer.wait_for_zone_data(timeout=12.0)
    if not zones_loaded:
        _LOGGER.warning("Timeout waiting for zone data - some zones may have incomplete information")
    
    _LOGGER.info("Waiting for group data (status, labels, sources, line inputs, volume)...")
    groups_loaded = await mixer.wait_for_group_data(timeout=12.0)
    if not groups_loaded:
        _LOGGER.warning("Timeout waiting for group data - some groups may not be available")
    
    # Mixer state is now fully populated. Build entities first, then register listener.
    entities = []
    zone_entities: dict[int, MixerZone] = {}
    group_entities: dict[int, MixerGroup] = {}

    # Setup the individual zone entities
    for zone_id, zone in mixer.zones_by_id.items():
        _LOGGER.debug("Setting up zone entity for zone_id: %s, %s", zone.id, zone.name)
        # Get enabled line inputs for this zone
        enabled_inputs = mixer.get_zone_enabled_line_inputs(zone_id)
        _LOGGER.info("DEBUG: Zone %s enabled_inputs returned: %s", zone_id, enabled_inputs)
        _LOGGER.info("DEBUG: Zone %s type: %s, bool: %s, len: %s", zone_id, type(enabled_inputs), bool(enabled_inputs), len(enabled_inputs) if enabled_inputs else 0)
        mixer_zone = MixerZone(zone.id, zone.name, mixer, use_zone_labels, entity_name_suffix, enabled_inputs, use_optimistic_volume, volume_db_range, paging_pre_delay_ms, paging_post_delay_ms, paging_usb_device)
        zone_entities[zone.id] = mixer_zone
        entities.append(mixer_zone)

    # Setup entities for enabled groups only
    _LOGGER.info("Checking groups for entity creation: %s groups found", len(mixer.groups_by_id))
    for group_id, group in mixer.groups_by_id.items():
        _LOGGER.info("Group %s: name='%s', enabled=%s, zones=%s", group.id, group.name, group.enabled, group.zones)
        if group.enabled:
            _LOGGER.info("Creating group entity for group_id: %s, %s (ENABLED)", group.id, group.name)
            # Get enabled line inputs for this group
            enabled_inputs = mixer.get_group_enabled_line_inputs(group_id)
            _LOGGER.info("DEBUG: Group %s enabled_inputs returned: %s", group_id, enabled_inputs)
            _LOGGER.info("DEBUG: Type of enabled_inputs: %s, bool check: %s", type(enabled_inputs), bool(enabled_inputs))
            mixer_group = MixerGroup(group.id, group.name, mixer, use_zone_labels, entity_name_suffix, enabled_inputs, use_optimistic_volume, volume_db_range, paging_pre_delay_ms, paging_post_delay_ms, paging_usb_device)
            group_entities[group.id] = mixer_group
            entities.append(mixer_group)
        else:
            _LOGGER.info("Skipping DISABLED group: group_id: %s, %s", group.id, group.name)

    # All entities created with current mixer state. Register listener for updates.
    mixer_listener = MixerListener(zone_entities, group_entities)
    mixer.register_listener(mixer_listener)
    
    _LOGGER.info("Total entities to add: %s", len(entities))
    async_add_entities(entities)

class MixerListener(MixerResponseListener):
    """Listener to direct messages to correct entities (zones, groups, and numbers)."""

    def __init__(
        self,
        zone_entities: dict[int, "MixerZone"] | None = None,
        group_entities: dict[int, "MixerGroup"] | None = None,
    ) -> None:
        self.mixer_zone_entities: dict[int, MixerZone] = zone_entities or {}
        self.mixer_group_entities: dict[int, MixerGroup] = group_entities or {}

    def connected(self):
        _LOGGER.warning("DCM1 Mixer reconnected")
        for entity in self.mixer_zone_entities.values():
            _LOGGER.debug("Restoring zone %s to available", entity)
            entity.set_available(True)
        for entity in self.mixer_group_entities.values():
            _LOGGER.debug("Restoring group %s to available", entity)
            entity.set_available(True)

    def disconnected(self):
        _LOGGER.warning("DCM1 Mixer disconnected")
        for entity in self.mixer_zone_entities.values():
            _LOGGER.debug("Updating zone %s to unavailable", entity)
            entity.set_available(False)
        for entity in self.mixer_group_entities.values():
            _LOGGER.debug("Updating group %s to unavailable", entity)
            entity.set_available(False)

    def source_label_received(self, source_id: int, label: str):
        _LOGGER.debug("Source label received for Source ID %s: %s", source_id, label)
        for entity in self.mixer_zone_entities.values():
            entity.update_source_list()
        for entity in self.mixer_group_entities.values():
            entity.update_source_list()

    def zone_label_received(self, zone_id: int, label: str):
        _LOGGER.debug("Zone label received for Zone ID %s: %s", zone_id, label)
        entity = self.mixer_zone_entities.get(zone_id)
        if entity:
            entity.set_name(label)

    def zone_line_inputs_received(self, zone_id: int, enabled_inputs: dict[int, bool]):
        _LOGGER.debug("Line inputs received for Zone ID %s: %s", zone_id, enabled_inputs)
        entity = self.mixer_zone_entities.get(zone_id)
        if entity:
            entity.update_enabled_inputs(enabled_inputs)

    def group_status_received(self, group_id: int, enabled: bool, zones: list[int]):
        _LOGGER.debug("Group status received for Group ID %s: enabled=%s, zones=%s", group_id, enabled, zones)
        # Note: If group is disabled at runtime, the entity will remain but won't receive updates

    def group_label_received(self, group_id: int, label: str):
        _LOGGER.debug("Group label received for Group ID %s: %s", group_id, label)
        entity = self.mixer_group_entities.get(group_id)
        if entity:
            entity.set_name(label)

    def group_line_inputs_received(self, group_id: int, enabled_inputs: dict[int, bool]):
        _LOGGER.debug("Group line inputs received for Group ID %s: %s", group_id, enabled_inputs)
        entity = self.mixer_group_entities.get(group_id)
        if entity:
            entity.update_enabled_inputs(enabled_inputs)

    def zone_source_received(self, zone_id: int, source_id: int):
        _LOGGER.debug("Source received for Zone ID %s: source ID %s", zone_id, source_id)
        entity = self.mixer_zone_entities.get(zone_id)
        if entity:
            _LOGGER.debug("Updating entity for source changed")
            entity.set_source(source_id)

    def zone_volume_level_received(self, zone_id: int, level):
        _LOGGER.debug("Volume level received for Zone ID %s: %s", zone_id, level)
        entity = self.mixer_zone_entities.get(zone_id)
        if entity:
            entity.maybe_update_volume_level_from_device(level)

    def group_source_received(self, group_id: int, source_id: int):
        _LOGGER.debug("Group source received for Group ID %s: source ID %s", group_id, source_id)
        entity = self.mixer_group_entities.get(group_id)
        if entity:
            entity.set_source(source_id)

    def group_volume_level_received(self, group_id: int, level):
        _LOGGER.debug("Group volume level received for Group ID %s: %s", group_id, level)
        entity = self.mixer_group_entities.get(group_id)
        if entity:
            entity.maybe_update_volume_level_from_device(level)

    def error(self, error_message: str):
        pass  # Not required for us

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
        | MediaPlayerEntityFeature.PLAY_MEDIA
    )
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER

    def __init__(self, zone_id, zone_name, mixer, use_zone_labels=True, entity_name_suffix="", enabled_line_inputs=None, use_optimistic_volume=True, volume_db_range=40, paging_pre_delay_ms=_PAGING_PRE_DELAY_MS_DEFAULT, paging_post_delay_ms=_PAGING_POST_DELAY_MS_DEFAULT, paging_usb_device=None) -> None:
        """Init."""
        self.zone_id = zone_id
        self._mixer: DCM1Mixer = mixer
        self._use_zone_labels = use_zone_labels
        self._entity_name_suffix = entity_name_suffix
        self._enabled_line_inputs: dict[int, bool] = enabled_line_inputs or {}
        self._use_optimistic_volume = use_optimistic_volume
        self._volume_db_range = max(1, min(61, volume_db_range))  # Clamp to valid range
        self._paging_pre_delay_ms: int = paging_pre_delay_ms
        self._paging_post_delay_ms: int = paging_post_delay_ms
        self._paging_usb_device: str | None = paging_usb_device
        
        _LOGGER.debug(f"Zone {zone_id} enabled_line_inputs: {self._enabled_line_inputs}")
        
        self._attr_source_list = self._build_source_list()
        self._attr_state = MediaPlayerState.ON
        self._volume_level = None  # Confirmed volume from device
        self._pending_volume = None  # User's uncommitted volume request
        self._pending_raw_volume_level = None  # Raw device level for pending request (0-62)
        self._pending_volume_rejected_count = 0  # Count of rejected volume responses (for timeout recovery)
        self._is_volume_muted = False
        self._raw_volume_level = None  # Last raw device volume level (0-62)
        self._pre_mute_volume = None  # HA volume level before muting (0.0-1.0)
        self._pre_mute_raw_volume = None  # Raw device level before muting (0-62)
        
        # Try to get initial source state
        initial_source_id = mixer.get_zone_source(zone_id)
        if initial_source_id and initial_source_id in mixer.sources_by_id:
            self._attr_source = mixer.sources_by_id[initial_source_id].name
        
        # Try to get initial volume level
        initial_volume = mixer.get_zone_volume_level(zone_id)
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
        unique_base = f"dcm1_{self._mixer._hostname.replace('.', '_')}"
        self._attr_unique_id = f"{unique_base}_zone{zone_id}"
        self._zone_name = zone_name

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info - computed dynamically so it stays current as zone name updates."""
        if self._use_zone_labels:
            display_name = self._zone_name
        else:
            display_name = f"Zone {self.zone_id}"
        
        if self._entity_name_suffix:
            display_name = f"{display_name} {self._entity_name_suffix}"

        return DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=display_name,
            manufacturer="Cloud Electronics",
            model="DCM1 Zone Mixer Zone",
        )

    def set_state(self, state):
        """Set the state."""
        self._attr_state = state
        self.schedule_update_ha_state()

    def set_available(self, available: bool):
        """Set availability for zone."""
        self._attr_available = available
        self.schedule_update_ha_state()

    def set_name(self, name: str):
        """Set the zone name."""
        self._zone_name = name
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

    def maybe_update_volume_level_from_device(self, level):
        """Maybe update volume state from device response (may reject stale responses).
        
        This is a read callback that updates state from device responses. It may not
        apply the update if the response appears to be stale (sent before our pending command).
        """
        # Parse level first so we can check staleness before modifying any state
        level_int = 62 if level == "mute" else int(level)
        
        # Check for stale response BEFORE modifying any state
        # Ignore stale responses during confirmation window (extends protocol debounce protection)
        # After protocol debounce completes and command is sent, there's a ~0.8s window until
        # confirmation response arrives. During this window, old heartbeat responses could arrive
        # from queries sent BEFORE our command. These stale responses would flip the slider back.
        # We reject them by only accepting responses that match our pending request.
        # Trade-off: Physical knob changes during this ~1s window are temporarily "lost" until
        # next heartbeat (~10s). Acceptable because typically only one person controls a zone at
        # a time, and they won't simultaneously adjust both the HA slider and physical knob.
        if self._pending_volume is not None and self._pending_raw_volume_level is not None:
            if level_int != self._pending_raw_volume_level:
                # Stale response from before our command - reject unless we've timed out
                self._pending_volume_rejected_count += 1
                # Retry strategy options: count 1 = wait/see (likely stale during confirmation),
                # count 2 = could re-issue command (mitigation), count 3 = give up (accept device)
                if self._pending_volume_rejected_count >= 3:
                    # After 3 rejections (~30s), our command may have been lost - accept device state
                    _LOGGER.warning(
                        f"Zone {self.zone_id}: Rejected 3 volume responses, "
                        f"our command may have been lost. Accepting device state."
                    )
                    self._pending_volume = None
                    self._pending_raw_volume_level = None
                    self._pending_volume_rejected_count = 0
                    # Fall through to accept this response
                else:
                    _LOGGER.debug(
                        f"Rejecting stale volume response for zone {self.zone_id}: "
                        f"got {level_int}, expecting {self._pending_raw_volume_level} "
                        f"(rejection {self._pending_volume_rejected_count}/3)"
                    )
                    return
        
        # Response accepted - now modify state
        if level == "mute":
            self._is_volume_muted = True
            self._attr_is_volume_muted = True
            self._raw_volume_level = 62
        else:
            self._is_volume_muted = False
            self._attr_is_volume_muted = False
            self._raw_volume_level = level_int
            
            if level_int >= self._volume_db_range:
                new_volume = 0.0  # Below usable range maps to 0%
            else:
                new_volume = 1.0 - (level_int / self._volume_db_range)
            
            # Check if this confirms a pending user request or is an external change
            # If we have a pending volume, check if device level matches what user requested
            if self._pending_volume is not None:
                # Use stored raw level for exact comparison (avoids recalculation)
                expected_level = self._pending_raw_volume_level
                
                if expected_level == level_int:
                    # Device confirmed user's request - commit pending to confirmed
                    self._volume_level = self._pending_volume
                    self._pending_volume = None
                    self._pending_raw_volume_level = None
                    self._pending_volume_rejected_count = 0
                    self._attr_volume_level = self._volume_level
                else:
                    # Device reports different level - external change (physical knob)
                    # Override pending with actual device state
                    self._volume_level = new_volume
                    self._pending_volume = None
                    self._pending_raw_volume_level = None
                    self._pending_volume_rejected_count = 0
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
        # Store both HA volume and raw device level for exact confirmation matching
        self._pending_volume = volume
        self._pending_raw_volume_level = level
        self._pending_volume_rejected_count = 0  # Reset counter on new command
        if self._use_optimistic_volume:
            self._attr_volume_level = volume  # UI shows pending state
            self.schedule_update_ha_state()  # Update UI immediately
        
        self._mixer.set_zone_volume(zone_id=self.zone_id, level=level)

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
            # Store both HA volume and raw device level before muting so we can restore exactly
            self._pre_mute_volume = self._volume_level
            self._pre_mute_raw_volume = self._raw_volume_level
            self._mixer.set_zone_volume(zone_id=self.zone_id, level=62)  # 62 = mute
        else:
            # Unmute to last known level before muting, or default to mid-range
            if self._pre_mute_raw_volume is not None:
                # Restore using raw device level (avoids rounding, preserves sub-minimum levels)
                level = self._pre_mute_raw_volume
                self._pre_mute_volume = None
                self._pre_mute_raw_volume = None
            elif self._volume_level is not None and self._volume_level > 0.0:
                # Fallback: recalculate from HA volume if no raw level stored
                # Linear: level = range * (1 - volume)
                level = round(self._volume_db_range * (1.0 - self._volume_level))
            else:
                level = self._volume_db_range // 2  # Default to mid-range if slider at 0% or unknown
            self._mixer.set_zone_volume(zone_id=self.zone_id, level=level)

    async def async_play_media(self, media_type: str, media_id: str, **kwargs) -> None:
        """Trigger paging sequence for this zone.

        Sequence:
          1. Resolve media-source:// URIs (e.g. TTS) to real URLs
          2. Probe media duration using ffprobe
          3. Open paging on this zone via '<PM,PAXXXXXXXX/>'
          4. Wait paging_pre_delay_ms for the paging input to stabilise
          5. Play the audio file/URL through the USB DI sound device
          6. Ensure paging stays open for at least the probed duration
          7. Wait paging_post_delay_ms for audio tail
          8. Close all paging via '<PM,PR/>'

        Args:
            media_type: Ignored (any value accepted).
            media_id:   Local file path or HTTP URL to the audio to play.
        """
        _LOGGER.info(
            "Zone %s: starting paging sequence for %s", self.zone_id, media_id
        )

        # Resolve media-source:// URIs (e.g. TTS) to real URLs for the subprocess
        if is_media_source_id(media_id):
            _LOGGER.debug("Resolving media source: %s", media_id)
            sourced_media = await async_resolve_media(self.hass, media_id, self.entity_id)
            media_id = sourced_media.url
            _LOGGER.debug("Resolved media source to: %s", media_id)

        # If the URL is relative, prepend the HA base URL
        if media_id.startswith("/"):
            base_url = get_url(self.hass, allow_internal=True)
            media_id = f"{base_url}{media_id}"
            _LOGGER.debug("Normalized relative URL to: %s", media_id)

        # Probe duration and download if necessary before starting paging
        duration, local_path = await _get_media_duration(self.hass, media_id, _LOGGER)
        if duration:
            _LOGGER.debug("Probed media duration: %s seconds", duration)
        
        try:
            self._mixer.start_zone_paging(self.zone_id)
            await asyncio.sleep(self._paging_pre_delay_ms / 1000)

            # Start timing the playback
            start_playback_time = self.hass.loop.time()
            
            # Use the local path (potentially the downloaded temp file) for playback
            await _play_paging_audio(local_path, self._paging_usb_device, _LOGGER)
            
            # Safety floor
            if duration:
                elapsed = self.hass.loop.time() - start_playback_time
                remaining = duration - elapsed
                if remaining > 0:
                    _LOGGER.debug("Playback process ended early, waiting %s remains", remaining)
                    await asyncio.sleep(remaining)

            await asyncio.sleep(self._paging_post_delay_ms / 1000)
        finally:
            self._mixer.stop_all_paging()
            # Cleanup temp file if we created one
            if local_path != media_id and os.path.exists(local_path):
                _LOGGER.debug("Cleaning up temp paging file: %s", local_path)
                try:
                    await self.hass.async_add_executor_job(os.remove, local_path)
                except Exception as exc: # noqa: BLE001
                    _LOGGER.warning("Failed to remove temp file %s: %s", local_path, exc)

        _LOGGER.info("Zone %s: paging sequence complete", self.zone_id)

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
        | MediaPlayerEntityFeature.PLAY_MEDIA
    )
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER

    def __init__(self, group_id, group_name, mixer, use_zone_labels=True, entity_name_suffix="", enabled_line_inputs=None, use_optimistic_volume=True, volume_db_range=40, paging_pre_delay_ms=_PAGING_PRE_DELAY_MS_DEFAULT, paging_post_delay_ms=_PAGING_POST_DELAY_MS_DEFAULT, paging_usb_device=None) -> None:
        """Init."""
        self.group_id = group_id
        self._mixer: DCM1Mixer = mixer
        self._use_zone_labels = use_zone_labels
        self._entity_name_suffix = entity_name_suffix
        self._enabled_line_inputs: dict[int, bool] = enabled_line_inputs or {}
        self._use_optimistic_volume = use_optimistic_volume
        self._volume_db_range = max(1, min(61, volume_db_range))  # Clamp to valid range
        self._paging_pre_delay_ms: int = paging_pre_delay_ms
        self._paging_post_delay_ms: int = paging_post_delay_ms
        self._paging_usb_device: str | None = paging_usb_device
        
        _LOGGER.debug(f"Group {group_id} enabled_line_inputs: {self._enabled_line_inputs}")
        
        self._attr_source_list = self._build_source_list()
        self._attr_state = MediaPlayerState.ON
        self._volume_level = None  # Confirmed volume from device
        self._pending_volume = None  # User's uncommitted volume request
        self._pending_raw_volume_level = None  # Raw device level for pending request (0-62)
        self._pending_volume_rejected_count = 0  # Count of rejected volume responses (for timeout recovery)
        self._is_volume_muted = False
        self._attr_is_volume_muted = False
        self._attr_volume_level = None
        self._raw_volume_level = None  # Last raw device volume level (0-62)
        self._pre_mute_volume = None  # HA volume level before muting (0.0-1.0)
        self._pre_mute_raw_volume = None  # Raw device level before muting (0-62)
        
        # Try to get initial source state
        initial_source_id = mixer.get_group_source(group_id)
        if initial_source_id and initial_source_id in mixer.sources_by_id:
            self._attr_source = mixer.sources_by_id[initial_source_id].name
            _LOGGER.info(f"Group {group_id} initial source: {initial_source_id} ({self._attr_source})")
        else:
            _LOGGER.warning(f"Group {group_id} initial source is None or invalid: {initial_source_id}")
        
        # Try to get initial volume level
        initial_volume = mixer.get_group_volume_level(group_id)
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
        unique_base = f"dcm1_{self._mixer._hostname.replace('.', '_')}"
        self._attr_unique_id = f"{unique_base}_group{group_id}"
        self._group_name = group_name

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info - computed dynamically so it stays current as group name updates."""
        if self._use_zone_labels:
            display_name = self._group_name
        else:
            display_name = f"Group {self.group_id}"
        
        if self._entity_name_suffix:
            display_name = f"{display_name} {self._entity_name_suffix}"

        return DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=display_name,
            manufacturer="Cloud Electronics",
            model="DCM1 Zone Mixer Group",
        )

    def set_state(self, state):
        """Set the state."""
        self._attr_state = state
        self.schedule_update_ha_state()

    def set_available(self, available: bool):
        """Set availability for group."""
        self._attr_available = available
        self.schedule_update_ha_state()

    def set_name(self, name: str):
        """Set the group name."""
        self._group_name = name
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

    def maybe_update_volume_level_from_device(self, level):
        """Maybe update volume state from device response (may reject stale responses).
        
        This is a read callback that updates state from device responses. It may not
        apply the update if the response appears to be stale (sent before our pending command).
        """
        # Parse level first so we can check staleness before modifying any state
        level_int = 62 if level == "mute" else int(level)
        
        # Check for stale response BEFORE modifying any state
        # See MixerZone.maybe_update_volume_level_from_device for detailed explanation
        if self._pending_volume is not None and self._pending_raw_volume_level is not None:
            if level_int != self._pending_raw_volume_level:
                # Stale response from before our command - reject unless we've timed out
                self._pending_volume_rejected_count += 1
                # Retry strategy options: count 1 = wait/see (likely stale during confirmation),
                # count 2 = could re-issue command (mitigation), count 3 = give up (accept device)
                if self._pending_volume_rejected_count >= 3:
                    # After 3 rejections (~30s), our command may have been lost - accept device state
                    _LOGGER.warning(
                        f"Group {self.group_id}: Rejected 3 volume responses, "
                        f"our command may have been lost. Accepting device state."
                    )
                    self._pending_volume = None
                    self._pending_raw_volume_level = None
                    self._pending_volume_rejected_count = 0
                    # Fall through to accept this response
                else:
                    _LOGGER.debug(
                        f"Rejecting stale volume response for group {self.group_id}: "
                        f"got {level_int}, expecting {self._pending_raw_volume_level} "
                        f"(rejection {self._pending_volume_rejected_count}/3)"
                    )
                    return
        
        # Response accepted - now modify state
        if level == "mute":
            self._is_volume_muted = True
            self._attr_is_volume_muted = True
            self._raw_volume_level = 62
        else:
            self._is_volume_muted = False
            self._attr_is_volume_muted = False
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
                # Use stored raw level for exact comparison (avoids recalculation)
                expected_level = self._pending_raw_volume_level
                
                if expected_level == level_int:
                    # Device confirmed our pending request - commit it
                    self._volume_level = self._pending_volume
                    self._pending_volume = None
                    self._pending_raw_volume_level = None
                    self._pending_volume_rejected_count = 0
                    self._attr_volume_level = self._volume_level
                else:
                    # Device reports different level - external change (physical control)
                    # Override pending with actual device state
                    self._volume_level = new_volume
                    self._pending_volume = None
                    self._pending_raw_volume_level = None
                    self._pending_volume_rejected_count = 0
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
        # Store both HA volume and raw device level for exact confirmation matching
        self._pending_volume = volume
        self._pending_raw_volume_level = level
        self._pending_volume_rejected_count = 0  # Reset counter on new command
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
            # Store both HA volume and raw device level before muting so we can restore exactly
            self._pre_mute_volume = self._volume_level
            self._pre_mute_raw_volume = self._raw_volume_level
            self._mixer.set_group_volume(group_id=self.group_id, level=62)  # 62 = mute
        else:
            # Unmute to last known level before muting, or default to mid-range
            if self._pre_mute_raw_volume is not None:
                # Restore using raw device level (avoids rounding, preserves sub-minimum levels)
                level = self._pre_mute_raw_volume
                self._pre_mute_volume = None
                self._pre_mute_raw_volume = None
            elif self._volume_level is not None and self._volume_level > 0.0:
                # Fallback: recalculate from HA volume if no raw level stored
                # Linear: level = range * (1 - volume)
                level = round(self._volume_db_range * (1.0 - self._volume_level))
            else:
                level = self._volume_db_range // 2  # Default to mid-range if slider at 0% or unknown
            self._mixer.set_group_volume(group_id=self.group_id, level=level)

    async def async_play_media(self, media_type: str, media_id: str, **kwargs) -> None:
        """Trigger paging sequence for all zones in this group.

        Sequence:
          1. Resolve media-source:// URIs (e.g. TTS) to real URLs
          2. Probe media duration using ffprobe
          3. Open paging on the group's zones
          4. Wait paging_pre_delay_ms for the paging inputs to stabilise
          5. Play the audio file/URL through the USB DI sound device
          6. Ensure paging stays open for at least the probed duration
          7. Wait paging_post_delay_ms for audio tail
          8. Close all paging via '<PM,PR/>'

        Args:
            media_type: Ignored (any value accepted).
            media_id:   Local file path or HTTP URL to the audio to play.
        """
        _LOGGER.info(
            "Group %s: starting paging sequence for %s", self.group_id, media_id
        )

        # Resolve media-source:// URIs (e.g. TTS) to real URLs for the subprocess
        if is_media_source_id(media_id):
            _LOGGER.debug("Resolving media source: %s", media_id)
            sourced_media = await async_resolve_media(self.hass, media_id, self.entity_id)
            media_id = sourced_media.url
            _LOGGER.debug("Resolved media source to: %s", media_id)

        # If the URL is relative, prepend the HA base URL
        if media_id.startswith("/"):
            base_url = get_url(self.hass, allow_internal=True)
            media_id = f"{base_url}{media_id}"
            _LOGGER.debug("Normalized relative URL to: %s", media_id)

        # Probe duration and download if necessary
        duration, local_path = await _get_media_duration(self.hass, media_id, _LOGGER)
        if duration:
            _LOGGER.debug("Probed media duration: %s seconds", duration)

        try:
            self._mixer.start_group_paging(self.group_id)
            await asyncio.sleep(self._paging_pre_delay_ms / 1000)

            # Start timing the playback
            start_playback_time = self.hass.loop.time()

            # Use local path for playback
            await _play_paging_audio(local_path, self._paging_usb_device, _LOGGER)

            # Safety floor
            if duration:
                elapsed = self.hass.loop.time() - start_playback_time
                remaining = duration - elapsed
                if remaining > 0:
                    _LOGGER.debug("Playback process ended early, waiting %s remains", remaining)
                    await asyncio.sleep(remaining)

            await asyncio.sleep(self._paging_post_delay_ms / 1000)
        finally:
            self._mixer.stop_all_paging()
            # Cleanup temp file
            if local_path != media_id and os.path.exists(local_path):
                _LOGGER.debug("Cleaning up temp paging file: %s", local_path)
                try:
                    await self.hass.async_add_executor_job(os.remove, local_path)
                except Exception as exc: # noqa: BLE001
                    _LOGGER.warning("Failed to remove temp file %s: %s", local_path, exc)

        _LOGGER.info("Group %s: paging sequence complete", self.group_id)
