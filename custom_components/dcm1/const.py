"""Constants for the Cloud DCM1 Zone Mixer integration."""

DOMAIN = "dcm1"

DEFAULT_PORT = 4999
DEFAULT_VOLUME_DB_RANGE = 40

CONF_ENTITY_NAME_SUFFIX = "entity_name_suffix"
CONF_USE_ZONE_LABELS = "use_zone_labels"
CONF_OPTIMISTIC_VOLUME = "optimistic_volume"
CONF_VOLUME_DB_RANGE = "volume_db_range"

CONF_PAGING_POST_DELAY_MS = "paging_post_delay_ms"
CONF_PAGING_USB_DEVICE = "paging_usb_device"
CONF_PAGING_STAGE_BEFORE_PLAY = "paging_stage_before_play"

CONF_INPUT_VOLUME_DEFAULTS = "input_volume_defaults"

DEFAULT_PAGING_POST_DELAY_MS = 2500

# Dispatcher signal fired when any zone's paging inclusion flag changes.
# Format with the config entry id: SIGNAL_PAGING_FLAGS_CHANGED.format(entry_id)
SIGNAL_PAGING_FLAGS_CHANGED = "dcm1_paging_flags_changed_{}"
