"""Home Assistant reminders for a confirmed LINE login failure."""

from __future__ import annotations

from asyncio import Event
from urllib.parse import quote

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations

from .api import GatewayStatus
from .const import CONF_ADDON_SLUG, CONF_CONNECTION_MODE, DOMAIN, MODE_ADDON


class LineLoginAlert:
    """One reminder per entry; LINE login never triggers API-key reauth."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize a reminder with an ID stable across integration reloads."""
        self.hass = hass
        self.entry = entry
        self.issue_id = f"line_login_required_{entry.entry_id}"
        self.notification_id = f"{DOMAIN}_{self.issue_id}"
        self._active = False
        self._seen_ready = False
        self._checked = False
        self._stopped = Event()

    @property
    def login_url(self) -> str:
        """Open the HA-managed add-on page, never its private container address."""
        slug = self.entry.data.get(CONF_ADDON_SLUG)
        if self.entry.data.get(CONF_CONNECTION_MODE) == MODE_ADDON and slug:
            return f"/hassio/addon/{quote(str(slug), safe='')}/info"
        return str(self.entry.data[CONF_URL])

    async def async_update(self, status: GatewayStatus) -> None:
        """Notify confirmed expiry; preserve a reminder through transient outages."""
        if self._stopped.is_set():
            return
        session_state = (status.session_status or "").lower()
        if (
            session_state == "ready" and status.line_logged_in is True
        ) or status.login_required_reason == "logged_out":
            if self._active or not self._checked:
                self.async_clear()
            self._seen_ready = session_state == "ready"
            self._checked = True
            return
        self._checked = True
        requires_login = status.line_logged_in is False and (
            session_state == "expired" or (session_state == "auth_required" and self._seen_ready)
        )
        if not requires_login or self._active:
            return
        placeholders = {"login_url": self.login_url}
        translations = await async_get_translations(
            self.hass, self.hass.config.language, "issues", [DOMAIN]
        )
        if self._stopped.is_set():
            return
        prefix = f"component.{DOMAIN}.issues.line_login_required"
        title = translations.get(f"{prefix}.title", "LINE OA needs you to sign in again")
        message = translations.get(
            f"{prefix}.description",
            "LINE login is no longer valid. Notifications cannot be sent. "
            "[Open LINE OA]({login_url}) and sign in again. "
            "This reminder clears after login is verified. Home Assistant pairing is unchanged.",
        ).format(**placeholders)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self.issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="line_login_required",
            translation_placeholders=placeholders,
        )
        persistent_notification.async_create(
            self.hass, message, title=title, notification_id=self.notification_id
        )
        self._active = True

    @callback
    def async_clear(self) -> None:
        """Clear reminders after verified recovery, explicit logout or unloading."""
        ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        persistent_notification.async_dismiss(self.hass, self.notification_id)
        self._active = False

    @callback
    def async_stop(self) -> None:
        """Prevent an in-flight status update from recreating an unloaded alert."""
        self._stopped.set()
        self.async_clear()
