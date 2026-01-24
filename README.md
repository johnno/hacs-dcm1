# hacs-dcm1

Home Assistant integration for the Cloud DCM1 Zone Mixer - Control 8 zones with 8 line sources over TCP/IP.

This custom integration provides comprehensive support for Cloud DCM1 Zone Mixer devices including source switching, volume control, zone/source labels, and line input filtering.

## Hardware Requirements

**Important:** The Cloud DCM1 has an RS-232 serial port, not an ethernet port. You need a **Serial-to-IP converter** to use this integration.

Tested with:
- **Waveshare RS232/485/422 TO POE ETH (B)** - Set "Enable Multi-host: No" for true pass-through mode

This integration should also work with the **DCM1e** model (which has native ethernet), but this is untested as I don't have access to one.

## Installation

### HACS (Recommended)

1. Open HACS in your Home Assistant instance
2. Go to Integrations
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add repository URL: `https://github.com/johnno/hacs-dcm1`
6. Select category: "Integration"
7. Click "Add"
8. Search for "Cloud DCM1" and install
9. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/dcm1` directory to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Configuration

### Initial Setup

1. Go to Settings -> Devices & Services
2. Click "+ Add Integration"
3. Search for "Cloud DCM1"
4. Enter your connection details:
   - **Host**: IP address of your Serial-to-IP converter (e.g., Waveshare) or DCM1e (e.g., `192.168.1.139`)
   - **Port**: TCP port (default: `4999`)
5. Configure optional settings (see below)
6. Each of the 8 zones will appear as a separate media player entity

### Configuration Options

After adding the integration, you can configure additional options:

#### Entity Name Suffix
**Default:** Zone number (e.g., `media_player.main_bar_1`)

Controls what gets appended to entity names:
- **Zone Number**: Append zone number - `media_player.main_bar_1`
- **Source Name**: Append current source name - `media_player.main_bar_music` (not recommended as source can change)
- **None**: No suffix - `media_player.main_bar` (warning: may cause conflicts if zone labels aren't unique)

#### Use Zone Labels for Entity Names
**Default:** ON

When enabled, entities are named using the zone labels from the DCM1:
- ON: `media_player.main_bar_1` (uses "Main Bar" label from zone 1)
- OFF: `media_player.zone_1` (generic zone names)

#### Line Input Filtering
**Automatic**

The integration automatically queries each zone to determine which line inputs are enabled. Only enabled inputs appear in the source list for that zone.

Example: If Zone 1 only has inputs 1, 7, and 8 enabled, the source selector will only show those three sources.

## Features

### Zone Control
- **8 Independent Zones**: Each zone appears as a separate media player entity
- **Dynamic Source Lists**: Only enabled line inputs shown per zone
- **Volume Control**: Set volume level (0-61dB attenuation, 62=mute)
- **Volume Buttons**: Increase/decrease volume controls
- **Mute Control**: Toggle mute state

### Real-time Updates
- **Label Querying**: Automatically fetches zone and source labels from DCM1
- **Background Polling**: Heartbeat queries every 60 seconds sync with physical panel changes
- **Command Confirmation**: Changes broadcast to all connected clients
- **Priority Queue**: User commands take priority over background polling

### Home Assistant Integration
- Full media player platform integration
- Source selection with filtered input lists
- Volume level display and control
- Mute state tracking
- Current source display

## Entity Naming

Entity IDs are automatically generated from zone labels:

- Zone label "Main Bar" → `media_player.main_bar_1`
- Zone label "Back Bar" → `media_player.back_bar_2`
- Zone label "Zone 4" → `media_player.zone_4_4`

Customize entity names in Home Assistant's entity settings if needed.

## Example Usage

### Automation Example
```yaml
automation:
  - alias: "Morning Music"
    trigger:
      - platform: time
        at: "07:00:00"
    action:
      - service: media_player.select_source
        target:
          entity_id: media_player.main_bar_1
        data:
          source: "Music"
      - service: media_player.volume_set
        target:
          entity_id: media_player.main_bar_1
        data:
          volume_level: 0.45  # -28dB
```

### Dashboard Card
```yaml
type: media-control
entity: media_player.main_bar_1
```

## Troubleshooting

### No zones appear after setup
- Verify DCM1 is accessible at the configured IP and port
- Check Home Assistant logs for connection errors
- Ensure Serial-to-IP converter is configured correctly (multi-host: OFF for Waveshare)

### Sources not showing
- Line input filtering may hide disabled inputs
- Check DCM1 configuration to enable line inputs for zones
- Query takes ~7 seconds on initial connection (8 zones × 8 inputs)

### Volume doesn't update from physical panel
- Background heartbeat polling runs every 60 seconds
- Physical changes will sync within 1 minute
- Check logs if updates aren't appearing

### Entity names conflict
- Enable "Entity Name Suffix" with "Zone Number" option
- Or ensure all zone labels in DCM1 are unique

## Technical Details

### Communication
- Uses persistent TCP connection to DCM1
- Commands use priority queue (user commands jump ahead of polling)
- 100ms minimum delay between commands
- Automatic reconnection on connection loss

### Protocol
- XML-style command protocol
- Fire-and-forget commands with query confirmation
- Supports zone/source/volume queries and commands

### Volume Scale
- DCM1 levels: 0-61 (maps to -0dB through -61dB attenuation)
- Level 62 = mute
- Home Assistant: 0.0-1.0 float (automatically converted)

## Requirements

- Home Assistant 2023.1 or newer
- Cloud DCM1 Zone Mixer accessible via TCP/IP
- Serial-to-IP converter (unless using DCM1e model)

## Support

For issues and feature requests, please use the GitHub issue tracker:
- Integration: https://github.com/johnno/hacs-dcm1/issues  
- Protocol library: https://github.com/johnno/pydcm1/issues
