# DCM1 EQ Support Design

## Overview
Add EQ (Equalizer) control to hacs-dcm1 using Home Assistant `number` entities. Each zone will have three number entities for treble, mid, and bass control.

## Entity Design

### Entity Type: `number`
- **Why**: Media Player doesn't support EQ controls. Number entities provide slider UI with proper min/max/step configuration.
- **Quantity**: 3 per zone (24 entities total for 8 zones)
- **Names**: 
  - `number.dcm1_{zone_name}_eq_treble`
  - `number.dcm1_{zone_name}_eq_mid`
  - `number.dcm1_{zone_name}_eq_bass`

### Number Entity Configuration
- **Range**: -14 to +14
- **Step**: 1 (device accepts even values only; odd values are rejected by validation)
- **Mode**: slider (for UI)
- **Unit**: dB (decibels)
- **Icon**: 
  - Treble: `mdi:equalizer-outline` or `mdi:sine-wave`
  - Mid: `mdi:equalizer`
  - Bass: `mdi:waveform`

## Implementation Plan

### Phase 1: Add Number Platform
1. Create `custom_components/dcm1/number.py`
2. Add `Platform.NUMBER` to `__init__.py` PLATFORMS list
3. Implement `DCM1ZoneEQ` number entity class
4. Handle listener callbacks for EQ updates

### Phase 2: Integration with Mixer
1. Add `zone_eq_received()` callback to MixerListener in media_player.py
2. Forward EQ updates to number entities
3. Ensure proper entity lifecycle (creation, updates, cleanup)

### Phase 3: Testing
1. Test harness updates to set EQ values
2. Verify with main.py status output
3. Test all three parameters independently
4. Test boundary values (0, ±14)
5. Test Home Assistant UI integration

## File Structure
```
custom_components/dcm1/
├── __init__.py          # Add Platform.NUMBER
├── media_player.py      # Existing zone/group entities
├── number.py            # NEW: EQ number entities
├── config_flow.py       # No changes needed
└── const.py             # No changes needed
```

## API Flow

### Setting EQ
```
HA UI → number.set_value → DCM1ZoneEQ.async_set_native_value()
  → mixer.set_zone_eq_treble|mid|bass(zone_id, value)
  → pydcm1 Zone.set_eq_treble|mid|bass()
  → Protocol command: <Z#.MU,T±N/> or <Z#.MU,M±N/> or <Z#.MU,B±N/>
```

### Receiving EQ Updates
```
Device → <z#.mu,eq, t=X, m=Y, b=Z/> (query response)
  → Protocol.zone_eq_received()
  → MixerListener.zone_eq_received() (in mixer.py)
  → DCM1ZoneEQ.update_value()

Device → <z#.mu,t=±N/> | <z#.mu,m=±N/> | <z#.mu,b=±N/> (set responses)
  → Protocol.zone_eq_*_received()
  → MixerListener.zone_eq_*_received()
  → DCM1ZoneEQ.update_value()
```

## Entity Relationships
- **Parent**: Each zone's media_player entity
- **Device**: Shared device_info with media_player
- **Unique ID**: `{entry_id}_zone_{zone_id}_eq_{parameter}`
- **Translation**: Use Home Assistant i18n for entity names

## Advantages of Number Entities
1. ✅ Native slider UI in Home Assistant
2. ✅ Proper min/max/step validation
3. ✅ Works with automations and scripts
4. ✅ Supports voice assistants ("Set bedroom treble to 5")
5. ✅ Clean separation from media_player concerns
6. ✅ Individual control + grouped control (via set_zone_eq)
