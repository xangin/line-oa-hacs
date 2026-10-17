"""Config flow for the LINE OA integration.

One integration, two connection modes that share the same API client,
actions and entities:

* ``external``: a Gateway on Ubuntu LXC / Docker / Linux. The user enters the
  Gateway URL and an API key (the URL can be pre-filled by zeroconf discovery
  or by probing this Home Assistant host).
* ``addon``: the Gateway app (add-on) announces itself via Supervisor
  discovery. After the user confirms, a one-time bootstrap token is exchanged
  for a dedicated API key. Nothing is created from discovery alone.

Only one active Gateway is supported, because the actions have no Gateway
selector. Switching between modes is always an explicit user choice.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import probatio as vol
from yarl import URL

from homeassistant.config_entries import (
    SOURCE_IGNORE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.hassio import is_hassio
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .addon import (
    AddonDiscovery,
    AddonDiscoveryError,
    async_find_discovery,
    bootstrap_token,
    parse_discovery,
)
from .api import (
    GatewayAuthError,
    GatewayResponseError,
    GatewayStatus,
    LineOAError,
    LineOAGatewayClient,
    PairingError,
    PairingRateLimitedError,
    PairingRejectedError,
    PairingUnavailableError,
    api_version_supported,
    normalize_base_url,
    parse_api_version,
)
from .const import (
    CONF_ADDON_SLUG,
    CONF_CONNECTION_MODE,
    CONF_DECLINED_ADDON,
    CONF_DISCOVERY_UUID,
    CONF_ENTITY_ID_BASE,
    CONF_INSTANCE_ID,
    CONF_PREVIOUS_CONNECTION,
    DEFAULT_GATEWAY_PORT,
    DEFAULT_NAME,
    DOMAIN,
    LOCAL_PROBE_TIMEOUT,
    LOGGER,
    MODE_ADDON,
    MODE_EXTERNAL,
)

URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class CannotConnect(Exception):
    """Gateway could not be reached."""


class InvalidAuth(Exception):
    """Gateway rejected the API key."""


class InvalidResponse(Exception):
    """Gateway returned an unexpected response."""


class InvalidUrl(Exception):
    """The Gateway URL is malformed."""


class IncompatibleApi(Exception):
    """Gateway reports an unsupported API version."""


class FlowError(Exception):
    """A config flow error; ``key`` is the translation key."""

    def __init__(self, key: str) -> None:
        """Initialize the error."""
        super().__init__(key)
        self.key = key


async def validate_gateway(
    hass: HomeAssistant, url: str, api_key: str
) -> tuple[str, GatewayStatus]:
    """Validate Gateway URL and API key. Return the normalized URL and status.

    A LINE session that is expired is *not* an error here: the Gateway
    connection is valid and the session problem is surfaced via entities.
    Gateways that do not report ``api_version`` (older LXC builds) are v1.
    """
    try:
        base_url = normalize_base_url(url)
    except ValueError as err:
        raise InvalidUrl from err
    client = LineOAGatewayClient(async_get_clientsession(hass), base_url, api_key)
    try:
        health = await client.async_health()
    except GatewayAuthError as err:
        raise InvalidAuth from err
    except LineOAError as err:
        LOGGER.debug("Gateway health check failed: %s", err)
        raise CannotConnect from err
    if not api_version_supported(parse_api_version(health.get("api_version"))):
        raise IncompatibleApi
    try:
        status = await client.async_get_status()
    except GatewayAuthError as err:
        raise InvalidAuth from err
    except GatewayResponseError as err:
        LOGGER.debug("Gateway status invalid: %s", err)
        raise InvalidResponse from err
    except LineOAError as err:
        LOGGER.debug("Gateway status failed: %s", err)
        raise CannotConnect from err
    if not api_version_supported(status.api_version):
        raise IncompatibleApi
    return base_url, status


async def async_gateway_answers(hass: HomeAssistant, url: str) -> bool:
    """Return True if a LINE OA Gateway answers ``/api/v1/health`` at ``url``.

    No API key is sent; a 401/403 still proves a Gateway is listening.
    """
    client = LineOAGatewayClient(async_get_clientsession(hass), url, "")
    try:
        async with asyncio.timeout(LOCAL_PROBE_TIMEOUT):
            await client.async_health()
    except GatewayAuthError:
        return True
    except LineOAError, TimeoutError:
        return False
    return True


def discovery_url(discovery_info: ZeroconfServiceInfo) -> str:
    """Build the Gateway base URL from a zeroconf record.

    Optional TXT records: ``scheme`` (``http``/``https``) and ``path``
    (base path behind a reverse proxy).
    """
    props = discovery_info.properties
    scheme = "https" if str(props.get("scheme", "")).lower() == "https" else "http"
    url = URL.build(
        scheme=scheme,
        host=discovery_info.host,
        port=discovery_info.port or DEFAULT_GATEWAY_PORT,
        path="/" + str(props.get("path") or "").strip("/"),
    )
    return normalize_base_url(str(url))


_VALIDATION_ERRORS: dict[type[Exception], str] = {
    InvalidUrl: "invalid_url",
    CannotConnect: "cannot_connect",
    InvalidAuth: "invalid_auth",
    IncompatibleApi: "incompatible_api",
    InvalidResponse: "invalid_response",
}
_VALIDATION_EXCEPTIONS = tuple(_VALIDATION_ERRORS)


def _connection_snapshot(entry: ConfigEntry) -> dict[str, Any]:
    """Return the settings kept for rollback when the user switches Gateway."""
    return {
        CONF_CONNECTION_MODE: entry.data.get(CONF_CONNECTION_MODE, MODE_EXTERNAL),
        CONF_URL: entry.data[CONF_URL],
        CONF_API_KEY: entry.data[CONF_API_KEY],
        CONF_INSTANCE_ID: entry.data.get(CONF_INSTANCE_ID, entry.unique_id),
    }


def _entity_id_base(entry: ConfigEntry) -> str:
    """Identity the entity/device unique IDs are built from (never changes)."""
    return str(entry.data.get(CONF_ENTITY_ID_BASE) or entry.unique_id or entry.entry_id)


def _switch_data(entry: ConfigEntry, new_data: dict[str, Any]) -> dict[str, Any]:
    """Entry data for an explicit Gateway switch: same entities, rollback kept."""
    return {
        **new_data,
        CONF_ENTITY_ID_BASE: _entity_id_base(entry),
        CONF_PREVIOUS_CONNECTION: _connection_snapshot(entry),
    }


class LineOAConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for LINE OA."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._discovered_url: str | None = None
        self._addon: AddonDiscovery | None = None

    # ------------------------------------------------------------------ helpers

    def _active_entries(self) -> list[ConfigEntry]:
        return self._async_current_entries(include_ignore=False)

    def _create_gateway_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Create the single Gateway entry and drop other pending LINE OA flows."""
        for flow in self.hass.config_entries.flow.async_progress_by_handler(DOMAIN):
            if flow["flow_id"] != self.flow_id:
                self.hass.config_entries.flow.async_abort(flow["flow_id"])
        return self.async_create_entry(title=DEFAULT_NAME, data=data)

    async def _async_validate(
        self, url: str, api_key: str, errors: dict[str, str]
    ) -> tuple[str, GatewayStatus] | None:
        try:
            return await validate_gateway(self.hass, url, api_key)
        except _VALIDATION_EXCEPTIONS as err:
            key = _VALIDATION_ERRORS[type(err)]
            errors[CONF_URL if key == "invalid_url" else "base"] = key
        except Exception:
            LOGGER.exception("Unexpected error validating the Gateway")
            errors["base"] = "unknown"
        return None

    async def _async_pair(self) -> tuple[dict[str, Any], GatewayStatus]:
        """Pair with the confirmed app and verify the issued credential.

        The discovery message is read again because the bootstrap token rotates
        (it is short-lived). Nothing secret is logged.
        """
        addon = self._addon
        if addon is None:
            raise FlowError("invalid_discovery_info")
        try:
            fresh = await async_find_discovery(self.hass, addon.instance_id)
            if fresh is None:
                raise FlowError("addon_not_found")
            current = parse_discovery(fresh)
        except AddonDiscoveryError as err:
            raise FlowError(str(err)) from err
        if current.slug != addon.slug or current.instance_id != addon.instance_id:
            raise FlowError("invalid_discovery_info")
        if not current.pairing_required:
            raise FlowError("addon_already_paired")
        if (token := bootstrap_token(fresh)) is None:
            raise FlowError("pairing_expired")

        client = LineOAGatewayClient(async_get_clientsession(self.hass), current.url, "")
        try:
            result = await client.async_pairing_exchange(current.instance_id, token)
        except PairingRejectedError as err:
            raise FlowError("pairing_rejected") from err
        except PairingRateLimitedError as err:
            raise FlowError("pairing_rate_limited") from err
        except PairingUnavailableError as err:
            raise FlowError("pairing_unavailable") from err
        except PairingError as err:
            raise FlowError("pairing_failed") from err

        try:
            base_url, status = await validate_gateway(self.hass, current.url, result.api_key)
        except _VALIDATION_EXCEPTIONS as err:
            raise FlowError(_VALIDATION_ERRORS[type(err)]) from err
        if status.instance_id != current.instance_id:
            raise FlowError("invalid_discovery_info")
        LOGGER.debug("Paired with LINE OA app %s", current.slug)
        return (
            {
                CONF_URL: base_url,
                CONF_API_KEY: result.api_key,
                CONF_CONNECTION_MODE: MODE_ADDON,
                CONF_INSTANCE_ID: current.instance_id,
                CONF_ADDON_SLUG: current.slug,
                CONF_DISCOVERY_UUID: current.discovery_uuid,
            },
            status,
        )

    async def _async_probe_local_gateway(self) -> str | None:
        """Look for a Gateway on this Home Assistant host's IP, default port."""
        try:
            internal_url = get_url(self.hass, allow_external=False, allow_cloud=False)
        except NoURLAvailableError:
            return None
        host = URL(internal_url).host
        if not host:
            return None
        candidate = str(URL.build(scheme="http", host=host, port=DEFAULT_GATEWAY_PORT))
        if await async_gateway_answers(self.hass, candidate):
            LOGGER.debug("Found a LINE OA Gateway on this host at %s", candidate)
            return candidate
        return None

    # ------------------------------------------------------------- user setup

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask how to connect: Home Assistant app or external Gateway."""
        if self._active_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_show_menu(step_id="user", menu_options=["addon", "external"])

    async def async_step_addon(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Use a Gateway app that Supervisor has announced; never pretend one exists."""
        if not is_hassio(self.hass):
            return self.async_abort(reason="addon_not_supported")
        try:
            info = await async_find_discovery(self.hass)
        except AddonDiscoveryError as err:
            return self.async_abort(reason=str(err))
        if info is None:
            return self.async_abort(reason="addon_not_found")
        return await self.async_step_hassio(info)

    async def async_step_external(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Connect to an external Gateway with URL and API key."""
        if self._active_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_validate(
                user_input[CONF_URL], user_input[CONF_API_KEY], errors
            )
            if result is not None:
                base_url, status = result
                await self.async_set_unique_id(status.instance_id)
                self._abort_if_unique_id_configured()
                return self._create_gateway_entry(
                    {
                        CONF_URL: base_url,
                        CONF_API_KEY: user_input[CONF_API_KEY],
                        CONF_CONNECTION_MODE: MODE_EXTERNAL,
                        CONF_INSTANCE_ID: status.instance_id,
                    }
                )

        suggested: Mapping[str, Any] | None = user_input
        if user_input is None and (local_url := await self._async_probe_local_gateway()):
            # Pre-fill the URL of a Gateway found on this HA host.
            suggested = {CONF_URL: local_url}

        return self.async_show_form(
            step_id="external",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_URL): URL_SELECTOR,
                        vol.Required(CONF_API_KEY): PASSWORD_SELECTOR,
                    }
                ),
                suggested,
            ),
            errors=errors,
        )

    # ------------------------------------------------- zeroconf (external)

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Handle an external Gateway announced via mDNS (``_line-oa-gw._tcp``)."""
        if self._active_entries():
            return self.async_abort(reason="single_instance_allowed")
        try:
            url = discovery_url(discovery_info)
        except ValueError:
            return self.async_abort(reason="invalid_discovery_info")

        if instance_id := str(discovery_info.properties.get("instance_id") or "").strip():
            await self.async_set_unique_id(instance_id)
            self._abort_if_unique_id_configured()
        self._async_abort_entries_match({CONF_URL: url})

        if not await async_gateway_answers(self.hass, url):
            return self.async_abort(reason="cannot_connect")

        self._discovered_url = url
        self.context["title_placeholders"] = {"name": URL(url).authority}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask only for the API key of a discovered external Gateway."""
        if self._discovered_url is None:
            return self.async_abort(reason="invalid_discovery_info")
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_validate(
                self._discovered_url, user_input[CONF_API_KEY], errors
            )
            if result is not None:
                base_url, status = result
                await self.async_set_unique_id(status.instance_id, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return self._create_gateway_entry(
                    {
                        CONF_URL: base_url,
                        CONF_API_KEY: user_input[CONF_API_KEY],
                        CONF_CONNECTION_MODE: MODE_EXTERNAL,
                        CONF_INSTANCE_ID: status.instance_id,
                    }
                )

        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): PASSWORD_SELECTOR}),
            description_placeholders={"url": self._discovered_url},
            errors=errors,
        )

    # ------------------------------------------------ Supervisor discovery

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        """Handle a Gateway app announced via Supervisor discovery.

        Only shows a confirmation; pairing starts after the user confirms.
        The Supervisor discovery UUID is stored separately from the Gateway
        ``instance_id`` (the unique ID), so removing the app never removes
        this entry.
        """
        try:
            addon = parse_discovery(discovery_info)
        except AddonDiscoveryError as err:
            return self.async_abort(reason=str(err))
        self._addon = addon

        existing = await self.async_set_unique_id(addon.instance_id)
        if (
            existing is not None
            and existing.source != SOURCE_IGNORE
            and existing.data.get(CONF_CONNECTION_MODE) == MODE_ADDON
        ):
            # Same app (restart / update / republish): refresh only the validated
            # Supervisor hostname; the credential is kept.
            self._abort_if_unique_id_configured(
                updates={
                    CONF_URL: addon.url,
                    CONF_ADDON_SLUG: addon.slug,
                    CONF_DISCOVERY_UUID: addon.discovery_uuid,
                }
            )
        self._abort_if_unique_id_configured()

        self.context["title_placeholders"] = {"name": addon.name}
        if active := self._active_entries():
            if active[0].data.get(CONF_DECLINED_ADDON) == addon.instance_id:
                return self.async_abort(reason="addon_declined")
            return await self.async_step_hassio_migrate()
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm connecting the app, then pair."""
        if self._addon is None:
            return self.async_abort(reason="invalid_discovery_info")
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data, _ = await self._async_pair()
            except FlowError as err:
                errors["base"] = err.key
            else:
                return self._create_gateway_entry(data)
        return self.async_show_form(
            step_id="hassio_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={"addon": self._addon.name},
            errors=errors,
        )

    async def async_step_hassio_migrate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer to keep the current Gateway or switch to the discovered app."""
        active = self._active_entries()
        if not active or self._addon is None:
            return self.async_abort(reason="invalid_discovery_info")
        return self.async_show_menu(
            step_id="hassio_migrate",
            menu_options=["migrate_keep", "migrate_switch"],
            description_placeholders={"addon": self._addon.name, "url": active[0].data[CONF_URL]},
        )

    async def async_step_migrate_keep(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Keep the current Gateway and stop prompting for this app."""
        active = self._active_entries()
        if not active or self._addon is None:
            return self.async_abort(reason="invalid_discovery_info")
        entry = active[0]
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_DECLINED_ADDON: self._addon.instance_id}
        )
        return self.async_abort(reason="kept_current")

    async def async_step_migrate_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explicitly switch the existing entry to the app (entities are kept)."""
        active = self._active_entries()
        if not active or self._addon is None:
            return self.async_abort(reason="invalid_discovery_info")
        entry = active[0]
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data, status = await self._async_pair()
            except FlowError as err:
                # The existing entry is untouched on any failure.
                errors["base"] = err.key
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=status.instance_id,
                    data=_switch_data(entry, data),
                    reason="migration_successful",
                )
        return self.async_show_form(
            step_id="migrate_switch",
            data_schema=vol.Schema({}),
            description_placeholders={
                "addon": self._addon.name,
                "url": entry.data[CONF_URL],
                "old_instance": str(entry.data.get(CONF_INSTANCE_ID, entry.unique_id)),
                "new_instance": self._addon.instance_id,
            },
            errors=errors,
        )

    # ------------------------------------------------------------ reconfigure

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change connection settings, re-pair, or switch connection mode."""
        entry = self._get_reconfigure_entry()
        if entry.data.get(CONF_CONNECTION_MODE) == MODE_ADDON:
            return self.async_show_menu(
                step_id="reconfigure", menu_options=["reconfigure_addon", "reconfigure_external"]
            )
        if is_hassio(self.hass):
            return self.async_show_menu(
                step_id="reconfigure", menu_options=["reconfigure_external", "reconfigure_addon"]
            )
        return await self.async_step_reconfigure_external()

    async def async_step_reconfigure_external(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the external Gateway URL/API key, or switch from the app to it."""
        entry = self._get_reconfigure_entry()
        switching = entry.data.get(CONF_CONNECTION_MODE) == MODE_ADDON
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input.get(CONF_API_KEY) or (
                None if switching else entry.data[CONF_API_KEY]
            )
            if not api_key:
                errors[CONF_API_KEY] = "api_key_required"
            elif result := await self._async_validate(user_input[CONF_URL], api_key, errors):
                base_url, status = result
                new_data = {
                    CONF_URL: base_url,
                    CONF_API_KEY: api_key,
                    CONF_CONNECTION_MODE: MODE_EXTERNAL,
                    CONF_INSTANCE_ID: status.instance_id,
                }
                if switching:
                    return self.async_update_reload_and_abort(
                        entry, unique_id=status.instance_id, data=_switch_data(entry, new_data)
                    )
                await self.async_set_unique_id(status.instance_id)
                self._abort_if_unique_id_mismatch(reason="wrong_gateway")
                return self.async_update_reload_and_abort(entry, data_updates=new_data)

        suggested_url: str | None
        if user_input is not None:
            suggested_url = user_input[CONF_URL]
        elif switching:
            previous = entry.data.get(CONF_PREVIOUS_CONNECTION) or {}
            suggested_url = (
                previous.get(CONF_URL)
                if previous.get(CONF_CONNECTION_MODE) == MODE_EXTERNAL
                else None
            )
        else:
            suggested_url = entry.data[CONF_URL]
        return self.async_show_form(
            step_id="reconfigure_external",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_URL): URL_SELECTOR,
                        vol.Optional(CONF_API_KEY): PASSWORD_SELECTOR,
                    }
                ),
                {CONF_URL: suggested_url} if suggested_url else None,
            ),
            errors=errors,
        )

    async def async_step_reconfigure_addon(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-pair with the app, or switch an external entry to the app."""
        entry = self._get_reconfigure_entry()
        if not is_hassio(self.hass):
            return self.async_abort(reason="addon_not_supported")
        is_addon = entry.data.get(CONF_CONNECTION_MODE) == MODE_ADDON
        if self._addon is None:
            try:
                info = await async_find_discovery(
                    self.hass, entry.data.get(CONF_INSTANCE_ID) if is_addon else None
                )
                if info is None:
                    return self.async_abort(reason="addon_not_found")
                self._addon = parse_discovery(info)
            except AddonDiscoveryError as err:
                return self.async_abort(reason=str(err))

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data, status = await self._async_pair()
            except FlowError as err:
                errors["base"] = err.key
            else:
                if is_addon and status.instance_id == entry.unique_id:
                    return self.async_update_reload_and_abort(entry, data_updates=data)
                return self.async_update_reload_and_abort(
                    entry, unique_id=status.instance_id, data=_switch_data(entry, data)
                )
        return self.async_show_form(
            step_id="reconfigure_addon",
            data_schema=vol.Schema({}),
            description_placeholders={"addon": self._addon.name, "url": entry.data[CONF_URL]},
            errors=errors,
        )

    # ----------------------------------------------------------------- reauth

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauthentication when the Gateway rejects the API key.

        Only for the Gateway API key; an expired LINE session is fixed in the
        Gateway Web UI and never triggers this flow.
        """
        if entry_data.get(CONF_CONNECTION_MODE) == MODE_ADDON:
            return await self.async_step_reauth_addon()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new external Gateway API key."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_validate(
                entry.data[CONF_URL], user_input[CONF_API_KEY], errors
            )
            if result is not None:
                _, status = result
                await self.async_set_unique_id(status.instance_id)
                self._abort_if_unique_id_mismatch(reason="wrong_gateway")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_KEY: user_input[CONF_API_KEY]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): PASSWORD_SELECTOR}),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )

    async def async_step_reauth_addon(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-pair with the app after its credential was reset in the app UI."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await async_find_discovery(self.hass, entry.data.get(CONF_INSTANCE_ID))
                if info is None:
                    raise FlowError("addon_not_found")
                self._addon = parse_discovery(info)
                if self._addon.instance_id != entry.unique_id:
                    raise FlowError("invalid_discovery_info")
                data, _ = await self._async_pair()
            except AddonDiscoveryError as err:
                errors["base"] = str(err)
            except FlowError as err:
                errors["base"] = err.key
            else:
                return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(
            step_id="reauth_addon", data_schema=vol.Schema({}), errors=errors
        )
