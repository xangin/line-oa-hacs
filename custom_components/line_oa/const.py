"""Constants for the LINE OA integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Final

DOMAIN: Final = "line_oa"
LOGGER = logging.getLogger(__package__)

DEFAULT_NAME: Final = "LINE OA"
DEFAULT_SCAN_INTERVAL: Final = timedelta(seconds=60)

# Config entry data keys (in addition to CONF_URL / CONF_API_KEY).
CONF_CONNECTION_MODE: Final = "connection_mode"
CONF_INSTANCE_ID: Final = "instance_id"
CONF_ADDON_SLUG: Final = "addon_slug"
CONF_DISCOVERY_UUID: Final = "discovery_uuid"
# Stable base for entity/device unique IDs; survives an explicit Gateway switch.
CONF_ENTITY_ID_BASE: Final = "entity_instance_id"
# Connection used before the last explicit switch, kept for rollback.
CONF_PREVIOUS_CONNECTION: Final = "previous_connection"
# Add-on instance the user chose not to switch to (stops repeated prompts).
CONF_DECLINED_ADDON: Final = "declined_addon_instance_id"

MODE_ADDON: Final = "addon"
MODE_EXTERNAL: Final = "external"

# Add-on repository users must add to the add-on store (shown in messages).
ADDON_REPOSITORY_URL: Final = "https://github.com/xangin/line-oa-ha-addon"

# The Gateway app listens on this fixed port on the Supervisor network.
ADDON_API_PORT: Final = 8787

# Actions (service names). "send_screen_shot" is intentionally spelled this way.
SERVICE_SEND_TEXT: Final = "send_text"
SERVICE_SEND_IMAGE: Final = "send_image"
SERVICE_SEND_SCREEN_SHOT: Final = "send_screen_shot"

ATTR_WHO: Final = "who"
ATTR_MESSAGE: Final = "message"
ATTR_IMAGE_PATH: Final = "image_path"
ATTR_CAMERA_ENTITY: Final = "camera_entity"

# Validation limits.
MAX_WHO_LENGTH: Final = 100
# LINE text messages are limited to 5000 characters.
MAX_MESSAGE_LENGTH: Final = 5000
MAX_IMAGE_BYTES: Final = 10 * 1024 * 1024  # 10 MiB
CAMERA_IMAGE_TIMEOUT: Final = 15  # seconds

# Home Assistant limits entity states to 255 characters.
MAX_STATE_LENGTH: Final = 255

# States reported by the Gateway itself.
LINE_SESSION_STATES: Final = [
    "ready",
    "auth_required",
    "expired",
    "disconnected",
    "error",
    "unknown",
]

# Combined HA -> Gateway -> LINE status; existing session state codes stay stable.
SESSION_STATES: Final = [
    *LINE_SESSION_STATES,
    "gateway_unreachable",
    "pairing_required",
    "status_unavailable",
    "instance_mismatch",
]

LAST_ERROR_NONE: Final = "none"
