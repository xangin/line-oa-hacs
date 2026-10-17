"""Image loading helpers for LINE OA actions.

Local files are read in the executor with symlink-safe path validation.
Camera snapshots come from the official ``camera.async_get_image`` API and
stay in memory only.
"""

from __future__ import annotations

from dataclasses import dataclass
import errno
import os
from pathlib import Path
import stat

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import CAMERA_IMAGE_TIMEOUT, DOMAIN, LOGGER, MAX_IMAGE_BYTES

JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
ALLOWED_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})

# Never readable, even if the user allowlists the whole config directory.
_DENIED_NAMES = frozenset({"secrets.yaml", "known_devices.yaml", "home-assistant_v2.db"})
_DENIED_DIRS = frozenset({".storage", ".cloud", ".ssh", "deps", "tts"})
_DENIED_SYSTEM_DIRS = ("/etc", "/private/etc", "/proc", "/sys", "/dev", "/run", "/root", "/ssl")


@dataclass(frozen=True, slots=True)
class LoadedImage:
    """Image bytes ready for upload."""

    content: bytes
    content_type: str
    filename: str


def detect_image_type(content: bytes) -> str | None:
    """Return the MIME type from magic bytes, or None if not JPEG/PNG."""
    if content.startswith(JPEG_MAGIC):
        return "image/jpeg"
    if content.startswith(PNG_MAGIC):
        return "image/png"
    return None


def _validation_error(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


def _allowed_base(hass: HomeAssistant, real_path: Path) -> Path | None:
    """Return the allowlisted directory (resolved) that contains ``real_path``.

    Uses Home Assistant's ``allowlist_external_dirs``, which by default holds
    the media directories (``/media`` on HA OS / container) and ``config/www``.
    """
    for allowed in hass.config.allowlist_external_dirs:
        base = Path(os.path.realpath(allowed))
        if real_path.is_relative_to(base):
            return base
    return None


def _is_denied(hass: HomeAssistant, real_path: Path, base: Path) -> bool:
    """Block sensitive files even inside an allowlisted directory."""
    if real_path.name in _DENIED_NAMES:
        return True
    # Hidden files/directories below the allowed directory (e.g. ".storage").
    if any(part.startswith(".") for part in real_path.relative_to(base).parts):
        return True
    config_dir = Path(os.path.realpath(hass.config.config_dir))
    if real_path.is_relative_to(config_dir):
        relative = real_path.relative_to(config_dir).parts
        if relative and (relative[0] in _DENIED_DIRS or any(p.startswith(".") for p in relative)):
            return True
    return any(real_path.is_relative_to(prefix) for prefix in _DENIED_SYSTEM_DIRS)


def _read_local_image(hass: HomeAssistant, image_path: str) -> LoadedImage:
    """Validate and read a local image. Runs in the executor."""
    if not os.path.isabs(image_path):
        raise _validation_error("image_path_not_absolute", path=image_path)

    # Resolve symlinks and ".." first, then validate the *real* location.
    real_path = Path(os.path.realpath(image_path))
    if not real_path.exists():
        raise _validation_error("image_not_found", path=image_path)
    base = _allowed_base(hass, real_path)
    if base is None or _is_denied(hass, real_path, base):
        raise _validation_error("image_path_not_allowed", path=image_path)
    if real_path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise _validation_error("image_invalid_format", path=image_path)

    # O_NOFOLLOW refuses a symlink swapped in after validation.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(real_path, flags)
    except FileNotFoundError:
        raise _validation_error("image_not_found", path=image_path) from None
    except OSError as err:
        if err.errno == errno.ELOOP:
            raise _validation_error("image_path_not_allowed", path=image_path) from None
        raise _validation_error("image_read_failed", path=image_path) from err

    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise _validation_error("image_not_found", path=image_path)
        if info.st_size > MAX_IMAGE_BYTES:
            raise _validation_error("image_too_large", path=image_path)
        with os.fdopen(fd, "rb", closefd=False) as file:
            content = file.read(MAX_IMAGE_BYTES + 1)
    except OSError as err:
        raise _validation_error("image_read_failed", path=image_path) from err
    finally:
        os.close(fd)

    if len(content) > MAX_IMAGE_BYTES:
        raise _validation_error("image_too_large", path=image_path)
    content_type = detect_image_type(content)
    if content_type is None:
        raise _validation_error("image_invalid_format", path=image_path)
    return LoadedImage(content, content_type, real_path.name)


async def async_load_local_image(hass: HomeAssistant, image_path: str) -> LoadedImage:
    """Load an image from the Home Assistant file system."""
    return await hass.async_add_executor_job(_read_local_image, hass, image_path)


async def async_capture_camera_image(hass: HomeAssistant, entity_id: str) -> LoadedImage:
    """Capture a fresh still image from a camera entity, kept in memory only."""
    state = hass.states.get(entity_id)
    if state is None:
        raise _validation_error("camera_not_found", entity_id=entity_id)
    if state.state == STATE_UNAVAILABLE:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="camera_unavailable",
            translation_placeholders={"entity_id": entity_id},
        )
    # Imported lazily: the camera integration (and its native requirements) is
    # loaded whenever a camera entity exists, but must not be required to load
    # this integration on systems without cameras.
    from homeassistant.components.camera import async_get_image  # noqa: PLC0415

    try:
        image = await async_get_image(hass, entity_id, timeout=CAMERA_IMAGE_TIMEOUT)
    except (HomeAssistantError, NotImplementedError) as err:
        LOGGER.debug("Camera %s did not return an image: %s", entity_id, err)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="camera_image_failed",
            translation_placeholders={"entity_id": entity_id},
        ) from err

    content = image.content
    if len(content) > MAX_IMAGE_BYTES:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="camera_image_too_large",
            translation_placeholders={"entity_id": entity_id},
        )
    content_type = detect_image_type(content)
    if content_type is None:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="camera_image_invalid_format",
            translation_placeholders={
                "entity_id": entity_id,
                "content_type": str(image.content_type),
            },
        )
    extension = "png" if content_type == "image/png" else "jpg"
    filename = f"{entity_id.split('.', 1)[1]}.{extension}"
    return LoadedImage(content, content_type, filename)
