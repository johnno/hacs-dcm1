"""Minimal harness to load hacs-dcm1 media_player without Home Assistant.

This stubs required Home Assistant modules and exercises MixerZone/MixerGroup
construction plus listener callbacks with a fake mixer.

Optional: connect to a real device and change zone volume.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import types
from pathlib import Path

HACS = Path(__file__).resolve().parents[1]  # .../dcm1/hacs-dcm1
PYDCM1 = HACS.parent / "pydcm1"

# Ensure local packages can be imported
sys.path.insert(0, str(PYDCM1))
sys.path.insert(0, str(HACS))

# ---- Home Assistant stubs -------------------------------------------------
class _MediaPlayerEntity:
    def schedule_update_ha_state(self):
        return None
    
    @property
    def volume_level(self):
        """Return volume level (0.0-1.0)."""
        return getattr(self, '_attr_volume_level', None)
    
    @property
    def source(self):
        """Return current source."""
        return getattr(self, '_attr_source', None)
    
    @property
    def source_list(self):
        """Return source list."""
        return getattr(self, '_attr_source_list', None)
    
    @property
    def is_volume_muted(self):
        """Return mute state."""
        return getattr(self, '_attr_is_volume_muted', False)
    
    @property
    def state(self):
        """Return state."""
        return getattr(self, '_attr_state', None)
    
    @property
    def extra_state_attributes(self):
        """Return extra attributes (debugging)."""
        return getattr(self, 'extra_state_attributes', {}) if hasattr(self, 'extra_state_attributes') else {}

class _MediaPlayerEntityFeature:
    SELECT_SOURCE = 1
    VOLUME_SET = 2
    VOLUME_STEP = 4
    VOLUME_MUTE = 8

class _MediaPlayerDeviceClass:
    RECEIVER = "receiver"

class _MediaPlayerState:
    ON = "on"
    OFF = "off"

class _ConfigEntry:
    def __init__(self, data):
        self.data = data
        self.entry_id = "test_entry"

class _HomeAssistant:
    def __init__(self):
        self.data = {}

class _AddEntitiesCallback:
    def __call__(self, entities):
        return None

class _Const:
    CONF_NAME = "name"
    CONF_HOST = "host"
    CONF_PORT = "port"

class _Platform:
    MEDIA_PLAYER = "media_player"
    NUMBER = "number"

class _NumberMode:
    SLIDER = "slider"

class _NumberEntity:
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_mode = _NumberMode.SLIDER
    _attr_native_min_value = -14
    _attr_native_max_value = 14
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "dB"
    _attr_native_value = None
    
    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
    
    def async_write_ha_state(self):
        pass

class _DeviceInfo:
    def __init__(self, identifiers=None, name=None, manufacturer=None, model=None, via_device=None):
        self.identifiers = identifiers
        self.name = name
        self.manufacturer = manufacturer
        self.model = model
        self.via_device = via_device

# ---- Inject number stubs -------------------------------------------------


homeassistant = types.ModuleType("homeassistant")
components = types.ModuleType("homeassistant.components")
media_player = types.ModuleType("homeassistant.components.media_player")
number = types.ModuleType("homeassistant.components.number")
config_entries = types.ModuleType("homeassistant.config_entries")
const = types.ModuleType("homeassistant.const")
core = types.ModuleType("homeassistant.core")
helpers = types.ModuleType("homeassistant.helpers")
helpers_entity = types.ModuleType("homeassistant.helpers.entity")
helpers_entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")

media_player.MediaPlayerDeviceClass = _MediaPlayerDeviceClass
media_player.MediaPlayerEntity = _MediaPlayerEntity
media_player.MediaPlayerEntityFeature = _MediaPlayerEntityFeature
media_player.MediaPlayerState = _MediaPlayerState
number.NumberEntity = _NumberEntity
number.NumberMode = _NumberMode
helpers_entity.DeviceInfo = _DeviceInfo
config_entries.ConfigEntry = _ConfigEntry
const.CONF_NAME = _Const.CONF_NAME
const.CONF_HOST = _Const.CONF_HOST
const.CONF_PORT = _Const.CONF_PORT
const.Platform = _Platform
core.HomeAssistant = _HomeAssistant
helpers_entity_platform.AddEntitiesCallback = _AddEntitiesCallback

sys.modules["homeassistant"] = homeassistant
sys.modules["homeassistant.components"] = components
sys.modules["homeassistant.components.media_player"] = media_player
sys.modules["homeassistant.components.number"] = number
sys.modules["homeassistant.config_entries"] = config_entries
sys.modules["homeassistant.const"] = const
sys.modules["homeassistant.core"] = core
sys.modules["homeassistant.helpers"] = helpers
sys.modules["homeassistant.helpers.entity"] = helpers_entity
sys.modules["homeassistant.helpers.entity_platform"] = helpers_entity_platform

# ---- Fake mixer ------------------------------------------------------------
class _FakeZone:
    def __init__(self, zone_id: int, name: str):
        self.id = zone_id
        self.name = name

class _FakeGroup:
    def __init__(self, group_id: int, name: str, enabled: bool = True, zones=None):
        self.id = group_id
        self.name = name
        self.enabled = enabled
        self.zones = zones or []

class _FakeSource:
    def __init__(self, source_id: int, name: str):
        self.id = source_id
        self.name = name

class _FakeProtocol:
    def __init__(self):
        self._zone_line_inputs_map = {}
        self._group_line_inputs_map = {}

    def get_zone_volume_level(self, zone_id: int):
        return 10

    def get_group_volume_level(self, group_id: int):
        return 12

    def get_group_source(self, group_id: int):
        return 1

    def get_group_enabled_line_inputs(self, group_id: int):
        return {1: True, 2: False, 3: True}

class _FakeMixer:
    def __init__(self):
        self.hostname = "dcm1.local"
        self.protocol = _FakeProtocol()
        self.zones_by_id = {1: _FakeZone(1, "Zone 1")}
        self.groups_by_id = {1: _FakeGroup(1, "Group 1", enabled=True, zones=[1])}
        self.sources_by_id = {
            1: _FakeSource(1, "Source 1"),
            2: _FakeSource(2, "Source 2"),
            3: _FakeSource(3, "Source 3"),
        }
        self.sources_by_name = {s.name: s for s in self.sources_by_id.values()}

    def get_zone_source(self, zone_id: int):
        return 1

    def get_zone_volume_level(self, zone_id: int):
        return 10

    def get_zone_enabled_line_inputs(self, zone_id: int):
        return {1: True, 2: False, 3: True}

    def get_group_source(self, group_id: int):
        return 1

    def get_group_volume_level(self, group_id: int):
        return 12

    def get_group_enabled_line_inputs(self, group_id: int):
        return {1: True, 2: False, 3: True}

    def set_zone_source(self, zone_id: int, source_id: int):
        return None

    def set_zone_volume(self, zone_id: int, level):
        return None

    def set_group_source(self, group_id: int, source_id: int):
        return None

    def set_group_volume(self, group_id: int, level):
        return None

# ---- Run harness -----------------------------------------------------------

async def _run_real_device(host: str, port: int, zone: int, level: int) -> None:
    """Test using low-level pydcm1 mixer API directly."""
    from pydcm1.mixer import DCM1Mixer

    print(f"Connecting to DCM1 at {host}:{port}...")
    mixer = DCM1Mixer(host, port, enable_heartbeat=False)
    
    try:
        await mixer.async_connect()
        print(f"Connected. Waiting for initial state to load...")
        await asyncio.sleep(2)
        print(f"Setting zone {zone} volume to {level}...")
        mixer.set_zone_volume(zone_id=zone, level=level)
        print("Command sent. Waiting for it to transmit...")
        await asyncio.sleep(1)
        print("Done.")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        mixer.close()
        print("Connection closed.")


async def _run_real_device_entity(host: str, port: int, zone: int, volume: float) -> None:
    """Test using high-level MixerZone media_player entity API."""
    from pydcm1.mixer import DCM1Mixer
    from custom_components.dcm1 import media_player as mp

    print(f"Connecting to DCM1 at {host}:{port}...")
    mixer = DCM1Mixer(host, port, enable_heartbeat=False)
    
    try:
        await mixer.async_connect()
        print(f"Connected. Querying mixer state...")
        mixer.query_status()
        
        print("Waiting for source labels...")
        await mixer.wait_for_source_labels(timeout=7.0)
        print("Waiting for zone data...")
        await mixer.wait_for_zone_data(timeout=12.0)
        print("Waiting for group data...")
        await mixer.wait_for_group_data(timeout=12.0)
        
        print(f"\nCreating MixerZone entity for zone {zone}...")
        zone_obj = mixer.zones_by_id.get(zone)
        if not zone_obj:
            print(f"ERROR: Zone {zone} not found!")
            return
        
        enabled_inputs = mixer.get_zone_enabled_line_inputs(zone)
        mixer_zone = mp.MixerZone(
            zone_id=zone_obj.id,
            zone_name=zone_obj.name,
            mixer=mixer,
            use_zone_labels=True,
            entity_name_suffix="",
            enabled_line_inputs=enabled_inputs,
            use_optimistic_volume=True,
            volume_db_range=40
        )
        
        # Create listener to update entity
        zone_entities = {zone: mixer_zone}
        listener = mp.MixerListener(zone_entities, {})
        mixer.register_listener(listener)
        
        print(f"Zone entity created: Zone {mixer_zone.zone_id} ({zone_obj.name})")
        print(f"Current volume (HA 0.0-1.0): {mixer_zone.volume_level}")
        print(f"Current source: {mixer_zone.source}")
        print(f"Available sources: {mixer_zone.source_list}")
        print(f"Is muted: {mixer_zone.is_volume_muted}")
        attrs = mixer_zone.extra_state_attributes
        if attrs:
            print(f"Extra attributes:")
            for key, value in attrs.items():
                print(f"  {key}: {value}")
        
        print(f"\nSetting volume to {volume} (0.0-1.0 range)...")
        # Calculate what level this should produce
        expected_level = round(40 * (1.0 - volume)) if volume > 0.0 else 40
        print(f"Expected device level: {expected_level} (from formula: 40 * (1.0 - {volume}))")
        mixer_zone.set_volume_level(volume)
        
        print("Waiting for command to transmit...")
        await asyncio.sleep(1)
        print(f"Volume after command (HA 0.0-1.0): {mixer_zone.volume_level}")
        print(f"Is muted: {mixer_zone.is_volume_muted}")
        attrs = mixer_zone.extra_state_attributes
        if attrs:
            print(f"Extra attributes after command:")
            for key, value in attrs.items():
                print(f"  {key}: {value}")
        # Verify the reverse calculation
        if mixer_zone.volume_level is not None:
            # Reverse: level = 40 * (1.0 - volume), so volume = 1.0 - (level / 40)
            print(f"Device should now be at level {expected_level}")
        print("Done.")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        mixer.close()
        print("Connection closed.")


async def _run_real_device_eq_entity(host: str, port: int, zone: int, treble: int = None, mid: int = None, bass: int = None) -> None:
    """Test EQ settings using high-level number entity API."""
    from pydcm1.mixer import DCM1Mixer
    from custom_components.dcm1 import media_player as mp
    from custom_components.dcm1 import number

    print(f"Connecting to DCM1 at {host}:{port}...")
    mixer = DCM1Mixer(host, port, enable_heartbeat=False)
    
    try:
        await mixer.async_connect()
        print(f"Connected. Waiting for initial state to load...")
        await asyncio.sleep(2)
        
        print(f"\nCreating MixerZone entity for zone {zone}...")
        zone_obj = mixer.zones_by_id.get(zone)
        if not zone_obj:
            print(f"ERROR: Zone {zone} not found!")
            return
        
        enabled_inputs = mixer.get_zone_enabled_line_inputs(zone)
        mixer_zone = mp.MixerZone(
            zone_id=zone_obj.id,
            zone_name=zone_obj.name,
            mixer=mixer,
            use_zone_labels=True,
            entity_name_suffix="",
            enabled_line_inputs=enabled_inputs,
            use_optimistic_volume=True,
            volume_db_range=40
        )
        
        # Create listener
        zone_entities = {zone: mixer_zone}
        
        # Wrap listener to add debug output
        class DebugMixerListener(mp.MixerListener):
            def zone_eq_treble_received(self, zone_id: int, treble: int):
                print(f"DEBUG CALLBACK: zone_eq_treble_received(zone_id={zone_id}, treble={treble:+d})")
                super().zone_eq_treble_received(zone_id, treble)
            
            def zone_eq_mid_received(self, zone_id: int, mid: int):
                print(f"DEBUG CALLBACK: zone_eq_mid_received(zone_id={zone_id}, mid={mid:+d})")
                super().zone_eq_mid_received(zone_id, mid)
            
            def zone_eq_bass_received(self, zone_id: int, bass: int):
                print(f"DEBUG CALLBACK: zone_eq_bass_received(zone_id={zone_id}, bass={bass:+d})")
                super().zone_eq_bass_received(zone_id, bass)
        
        listener = DebugMixerListener(zone_entities, {})
        mixer.register_listener(listener)
        
        print(f"Zone entity created: Zone {mixer_zone.zone_id} ({zone_obj.name})")
        print(f"DEBUG - Debug listener registered")
        
        # Create EQ number entities
        print(f"\nCreating EQ number entities...")
        eq_entities = {}
        for parameter in ["treble", "mid", "bass"]:
            entity = number.DCM1ZoneEQ(
                zone_id=zone_obj.id,
                zone_name=zone_obj.name,
                parameter=parameter,
                mixer=mixer,
                config_entry_id="test_entry",
                device_name="DCM1",
                use_zone_labels=True,
                entity_name_suffix="",
            )
            # Register with parent zone
            mixer_zone.register_eq_entity(parameter, entity)
            eq_entities[parameter] = entity
            print(f"  Created EQ {parameter} entity: {entity._attr_unique_id}")
        
        # Query current EQ settings for this specific zone
        print(f"\nQuerying current EQ settings for zone {zone}...")
        print("DEBUG - Sending query_status...")
        mixer.query_status()
        print("DEBUG - Waiting 5 seconds for responses...")
        await asyncio.sleep(5)
        
        # Debug: Check what's in the zone object directly
        print(f"\nDEBUG - Zone object values from mixer after 5 sec wait:")
        print(f"  zone_obj.eq_treble: {zone_obj.eq_treble}")
        print(f"  zone_obj.eq_mid: {zone_obj.eq_mid}")
        print(f"  zone_obj.eq_bass: {zone_obj.eq_bass}")
        
        # Read initial values from entities
        print(f"\nInitial EQ values for zone {zone} from entities:")
        for parameter in ["treble", "mid", "bass"]:
            entity = eq_entities[parameter]
            value = entity._attr_native_value if entity._attr_native_value is not None else 0
            print(f"  {parameter.capitalize()}: {value:+d} dB")
        
        # Set EQ values if provided
        if treble is not None or mid is not None or bass is not None:
            if treble is not None:
                print(f"\nSetting zone {zone} EQ treble to {treble:+d}...")
                await eq_entities["treble"].async_set_native_value(float(treble))
                await asyncio.sleep(1)
            
            if mid is not None:
                print(f"Setting zone {zone} EQ mid to {mid:+d}...")
                await eq_entities["mid"].async_set_native_value(float(mid))
                await asyncio.sleep(1)
            
            if bass is not None:
                print(f"Setting zone {zone} EQ bass to {bass:+d}...")
                await eq_entities["bass"].async_set_native_value(float(bass))
                await asyncio.sleep(1)
            
            print("Waiting for device callbacks...")
            await asyncio.sleep(1)
            
            # Read updated values from entities
            print(f"Updated EQ values from entities:")
            for parameter in ["treble", "mid", "bass"]:
                entity = eq_entities[parameter]
                print(f"  {parameter.capitalize()}: {entity._attr_native_value:+d} dB")
        
        print("Done.")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        mixer.close()
        print("Connection closed.")


async def _run_real_device_eq(host: str, port: int, zone: int, treble: int = None, mid: int = None, bass: int = None) -> None:
    """Test EQ settings using low-level pydcm1 mixer API directly."""
    from pydcm1.mixer import DCM1Mixer

    print(f"Connecting to DCM1 at {host}:{port}...")
    mixer = DCM1Mixer(host, port, enable_heartbeat=False)
    
    try:
        await mixer.async_connect()
        print(f"Connected. Waiting for initial state to load...")
        await asyncio.sleep(2)
        
        # Query current EQ settings
        print(f"\nQuerying current EQ settings for zone {zone}...")
        mixer.query_status()
        await asyncio.sleep(2)  # Wait longer for EQ query responses
        
        zone_obj = mixer.zones_by_id.get(zone)
        if zone_obj:
            treble_str = f"{zone_obj.eq_treble:+d}" if zone_obj.eq_treble is not None else "N/A"
            mid_str = f"{zone_obj.eq_mid:+d}" if zone_obj.eq_mid is not None else "N/A"
            bass_str = f"{zone_obj.eq_bass:+d}" if zone_obj.eq_bass is not None else "N/A"
            print(f"Current EQ - Treble: {treble_str}, Mid: {mid_str}, Bass: {bass_str}")
        
        # If setting all three parameters, use combined method
        if treble is not None and mid is not None and bass is not None:
            print(f"\nSetting zone {zone} EQ to T:{treble:+d}, M:{mid:+d}, B:{bass:+d}...")
            mixer.set_zone_eq(zone_id=zone, treble=treble, mid=mid, bass=bass)
            await asyncio.sleep(2)  # Wait longer for command to complete
        else:
            # Set individual EQ parameters if provided
            # Note: Individual setters require all values to be known
            if treble is not None:
                print(f"\nSetting zone {zone} EQ treble to {treble:+d}...")
                mixer.set_zone_eq_treble(zone_id=zone, level=treble)
                await asyncio.sleep(1)
            
            if mid is not None:
                print(f"Setting zone {zone} EQ mid to {mid:+d}...")
                mixer.set_zone_eq_mid(zone_id=zone, level=mid)
                await asyncio.sleep(1)
            
            if bass is not None:
                print(f"Setting zone {zone} EQ bass to {bass:+d}...")
                mixer.set_zone_eq_bass(zone_id=zone, level=bass)
                await asyncio.sleep(1)
        
        # Query again to verify
        print(f"\nVerifying EQ settings...")
        mixer.query_status()
        await asyncio.sleep(2)  # Wait longer for query responses
        
        zone_obj = mixer.zones_by_id.get(zone)
        if zone_obj:
            treble_str = f"{zone_obj.eq_treble:+d}" if zone_obj.eq_treble is not None else "N/A"
            mid_str = f"{zone_obj.eq_mid:+d}" if zone_obj.eq_mid is not None else "N/A"
            bass_str = f"{zone_obj.eq_bass:+d}" if zone_obj.eq_bass is not None else "N/A"
            print(f"Updated EQ - Treble: {treble_str}, Mid: {mid_str}, Bass: {bass_str}")
        
        print("Done.")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        mixer.close()
        print("Connection closed.")


def _run_stub_harness() -> None:
    from custom_components.dcm1 import media_player as mp

    mixer = _FakeMixer()

    zone = mp.MixerZone(1, "Zone 1", mixer, True, "", mixer.get_zone_enabled_line_inputs(1), True, 40)
    group = mp.MixerGroup(1, "Group 1", mixer, True, "", mixer.get_group_enabled_line_inputs(1), True, 40)

    listener = mp.MixerListener({1: zone}, {1: group})
    listener.zone_label_received(1, "Zone 1A")
    listener.group_label_received(1, "Group 1A")
    listener.zone_line_inputs_received(1, {1: True, 2: True, 3: False})
    listener.group_line_inputs_received(1, {1: True, 2: True, 3: False})
    listener.zone_source_received(1, 2)
    listener.group_source_received(1, 2)
    listener.zone_volume_level_received(1, 12)
    listener.group_volume_level_received(1, 12)

    print("Harness OK: entities constructed and listener callbacks executed.")


def main():
    print("=== hacs-dcm1 test harness starting ===")
    parser = argparse.ArgumentParser(description="hacs-dcm1 test harness")
    parser.add_argument("--host", help="DCM1 host/IP for real device test")
    parser.add_argument("--port", type=int, default=4999, help="DCM1 port (default 4999)")
    parser.add_argument("--zone", type=int, default=8, help="Zone ID for volume test (default 8)")
    parser.add_argument("--level", type=int, help="Volume level 0-61 or 62=mute (low-level test only)")
    parser.add_argument("--volume", type=float, help="Volume 0.0-1.0 (entity test only, default 0.5)")
    parser.add_argument("--entity", action="store_true", help="Test MixerZone entity API instead of low-level mixer")
    parser.add_argument("--eq-treble", type=int, help="Set EQ treble (-14 to +14)")
    parser.add_argument("--eq-mid", type=int, help="Set EQ mid (-14 to +14)")
    parser.add_argument("--eq-bass", type=int, help="Set EQ bass (-14 to +14)")
    parser.add_argument("--eq-entity", action="store_true", help="Test EQ via number entities instead of low-level API")
    args = parser.parse_args()

    print(f"Args: host={args.host}, port={args.port}, zone={args.zone}, level={args.level}, volume={args.volume}, entity={args.entity}, eq_entity={args.eq_entity}")

    if args.host:
        print(f"Running real device test...")
        
        # Check if EQ parameters are provided
        if args.eq_treble is not None or args.eq_mid is not None or args.eq_bass is not None:
            # Test EQ functionality
            if args.eq_entity:
                # Test EQ via number entities
                asyncio.run(_run_real_device_eq_entity(args.host, args.port, args.zone, args.eq_treble, args.eq_mid, args.eq_bass))
            else:
                # Test EQ via low-level mixer API
                asyncio.run(_run_real_device_eq(args.host, args.port, args.zone, args.eq_treble, args.eq_mid, args.eq_bass))
        elif args.entity:
            # Test entity API
            volume = args.volume if args.volume is not None else 0.5
            asyncio.run(_run_real_device_entity(args.host, args.port, args.zone, volume))
        else:
            # Test low-level API
            level = args.level if args.level is not None else 20
            asyncio.run(_run_real_device(args.host, args.port, args.zone, level))
    else:
        print("Running stub harness...")
        _run_stub_harness()


if __name__ == "__main__":
    main()
