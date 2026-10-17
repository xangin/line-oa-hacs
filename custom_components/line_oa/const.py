"""Constants for the LINE OA integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Final

DOMAIN: Final = "line_oa"
LOGGER = logging.getLogger(__package__)

DEFAULT_NAME: Final = "LINE OA"
# Default Gateway port, used for zeroconf records without a port and for the
# "Gateway on this Home Assistant host" probe in the manual setup form.
DEFAULT_GATEWAY_PORT: Final = 8790
LOCAL_PROBE_TIMEOUT: Final = 3  # seconds
DEFAULT_SCAN_INTERVAL: Final = timedelta(seconds=60)

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

# Gateway session states exposed by sensor.line_oa_session_status.
SESSION_STATES: Final = [
    "ready",
    "auth_required",
    "expired",
    "disconnected",
    "error",
    "unknown",
]

LAST_ERROR_NONE: Final = "none"
