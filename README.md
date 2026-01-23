# hacs-dcm1

Integration that supports Cloud DCM1 Zone Mixer.

This Home Assistant custom integration provides support for controlling Cloud DCM1 Zone Mixer devices with 8 zones and 8 line sources.

## Installation

### HACS (Recommended)

1. Open HACS in your Home Assistant instance
2. Add this repository as a custom repository
3. Search for "DCM1" and install

### Manual Installation

1. Copy the `custom_components/dcm1` directory to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Configuration

1. Go to Settings -> Devices & Services
2. Click "+ Add Integration"
3. Search for "Cloud DCM1"
4. Enter the IP address and port (default 4999) of your DCM1 mixer
5. Each of the 8 zones will appear as a separate media player entity

## Features

- **8 Zone Control**: Each zone appears as a separate media player entity
- **Source Switching**: Select from 8 line source inputs for each zone
- **Volume Control**: Set volume level, increase/decrease, or mute
- **Real-time Updates**: Zone labels, source labels, and volume changes reflected immediately
- **Home Assistant Media Player Integration**: Full integration with HA's media player platform

## Supported Operations

- Select source (input) for each zone
- Set volume level (0-61dB range)
- Volume up/down controls
- Mute/unmute
- View current zone status

## Requirements

- Home Assistant 2023.1 or newer
- Cloud DCM1 Zone Mixer on local network
- DCM1 must be accessible via TCP/IP on port 4999 (default)

## Support

For issues and feature requests, please use the GitHub issue tracker at https://github.com/johnno/hacs-dcm1/issues
