"""Supervisor discovery handling for the LINE OA Gateway app (add-on).

The app announces itself with a Supervisor discovery message for the
``line_oa`` service. Only Home Assistant Core can read these messages, and
while the app is not paired the message carries a short-lived, single-use
``bootstrap_token``. The token is exchanged for a dedicated API key through
the regular Gateway API client (``LineOAGatewayClient.async_pairing_exchange``).

The connection target is never taken verbatim from the message: the host must
be the Supervisor-assigned hostname of the announcing app (derived from its
slug, which Supervisor itself provides) and the port is fixed. This prevents a
discovery message from steering Home Assistant to arbitrary hosts (SSRF).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import TYPE_CHECKING, Any
from uuid import UUID

from homeassistant.core import HomeAssistant
from homeassistant.helpers.hassio import is_hassio
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from homeassistant.util import dt as dt_util

from .api import SUPPORTED_API_VERSION, parse_api_version
from .const import ADDON_API_PORT, DOMAIN

if TYPE_CHECKING:
    from aiohasupervisor import SupervisorClient

_SLUG = re.compile(r"[a-z0-9]+_line_oa")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")


class AddonDiscoveryError(Exception):
    """Discovery data is unusable; ``str(err)`` is a translation key."""


@dataclass(frozen=True, slots=True)
class AddonDiscovery:
    """Validated, non-secret identity of a Gateway app."""

    instance_id: str
    url: str
    slug: str
    name: str
    discovery_uuid: str
    pairing_required: bool = field(default=False)


def addon_url(host: str) -> str:
    """Return the internal URL of the app on the Supervisor network."""
    return f"http://{host}:{ADDON_API_PORT}"


def parse_discovery(info: HassioServiceInfo) -> AddonDiscovery:
    """Validate a discovery message without trusting any field blindly."""
    config: Any = info.config
    if not isinstance(config, dict) or not isinstance(info.slug, str):
        raise AddonDiscoveryError("invalid_discovery_info")
    # The app always reports its API version; a missing value is not "legacy v1".
    if parse_api_version(config.get("api_version")) != SUPPORTED_API_VERSION:
        raise AddonDiscoveryError("incompatible_api")
    host = config.get("host")
    port = config.get("port")
    expected_host = info.slug.replace("_", "-")
    if (
        not _SLUG.fullmatch(info.slug)
        or host != expected_host
        or isinstance(port, bool)
        or port != ADDON_API_PORT
    ):
        raise AddonDiscoveryError("invalid_discovery_info")
    try:
        instance_id = str(UUID(str(config["instance_id"])))
        discovery_uuid = UUID(str(info.uuid)).hex
    except (KeyError, ValueError, TypeError) as err:
        raise AddonDiscoveryError("invalid_discovery_info") from err
    return AddonDiscovery(
        instance_id=instance_id,
        url=addon_url(expected_host),
        slug=info.slug,
        name=str(info.name or "LINE OA"),
        discovery_uuid=discovery_uuid,
        pairing_required=config.get("pairing_required") is not False,
    )


def bootstrap_token(info: HassioServiceInfo) -> str | None:
    """Return the one-time pairing token if present, well-formed and unexpired."""
    token = info.config.get("bootstrap_token")
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        return None
    expires = info.config.get("bootstrap_expires_at")
    if isinstance(expires, str):
        expires_at: datetime | None = dt_util.parse_datetime(expires)
        if expires_at is not None and expires_at <= dt_util.utcnow():
            return None
    return token


def _get_supervisor_client(hass: HomeAssistant) -> SupervisorClient:
    """Return the Core Supervisor client (imported lazily: Supervisor only)."""
    from homeassistant.components.hassio import get_supervisor_client  # noqa: PLC0415

    return get_supervisor_client(hass)


async def async_find_discovery(
    hass: HomeAssistant, instance_id: str | None = None
) -> HassioServiceInfo | None:
    """Read the current ``line_oa`` discovery message from Supervisor.

    Used after the user confirms (the token in the original flow data may have
    rotated) and when the user picks "Home Assistant app" manually. Returns
    ``None`` on installs without Supervisor or when no app announced itself.
    """
    if not is_hassio(hass):
        return None
    from aiohasupervisor import SupervisorError  # noqa: PLC0415

    try:
        records = await _get_supervisor_client(hass).discovery.list()
    except (SupervisorError, TimeoutError) as err:
        raise AddonDiscoveryError("cannot_connect") from err
    for record in records:
        if record.service != DOMAIN:
            continue
        if instance_id is not None and record.config.get("instance_id") != instance_id:
            continue
        return HassioServiceInfo(
            config=dict(record.config),
            name="LINE OA",
            slug=record.addon,
            uuid=record.uuid.hex,
        )
    return None
