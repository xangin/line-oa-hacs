"""Config flow for the LINE OA integration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import probatio as vol
from yarl import URL

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import (
    GatewayAuthError,
    GatewayResponseError,
    GatewayStatus,
    LineOAError,
    LineOAGatewayClient,
    normalize_base_url,
)
from .const import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_NAME,
    DOMAIN,
    LOCAL_PROBE_TIMEOUT,
    LOGGER,
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


async def validate_gateway(
    hass: HomeAssistant, url: str, api_key: str
) -> tuple[str, GatewayStatus]:
    """Validate Gateway URL and API key. Return the normalized URL and status.

    A LINE session that is expired is *not* an error here: the Gateway
    connection is valid and the session problem is surfaced via entities.
    """
    try:
        base_url = normalize_base_url(url)
    except ValueError as err:
        raise InvalidUrl from err
    client = LineOAGatewayClient(async_get_clientsession(hass), base_url, api_key)
    try:
        await client.async_health()
    except GatewayAuthError as err:
        raise InvalidAuth from err
    except LineOAError as err:
        LOGGER.debug("Gateway health check failed: %s", err)
        raise CannotConnect from err
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


class LineOAConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for LINE OA."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._discovered_url: str | None = None

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

    async def _async_validate(
        self, url: str, api_key: str, errors: dict[str, str]
    ) -> tuple[str, GatewayStatus] | None:
        try:
            return await validate_gateway(self.hass, url, api_key)
        except InvalidUrl:
            errors[CONF_URL] = "invalid_url"
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except InvalidResponse:
            errors["base"] = "invalid_response"
        except Exception:
            LOGGER.exception("Unexpected error validating the Gateway")
            errors["base"] = "unknown"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_validate(
                user_input[CONF_URL], user_input[CONF_API_KEY], errors
            )
            if result is not None:
                base_url, status = result
                await self.async_set_unique_id(status.instance_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=DEFAULT_NAME,
                    data={CONF_URL: base_url, CONF_API_KEY: user_input[CONF_API_KEY]},
                )

        suggested: Mapping[str, Any] | None = user_input
        if user_input is None and (local_url := await self._async_probe_local_gateway()):
            # Pre-fill the URL of a Gateway found on this HA host.
            suggested = {CONF_URL: local_url}

        return self.async_show_form(
            step_id="user",
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

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Handle a Gateway announced via mDNS (``_line-oa-gw._tcp``).

        The URL is taken from the announcement, so the user only enters the
        Gateway API key.
        """
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
        """Ask only for the API key of a discovered Gateway."""
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
                return self.async_create_entry(
                    title=DEFAULT_NAME,
                    data={CONF_URL: base_url, CONF_API_KEY: user_input[CONF_API_KEY]},
                )

        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): PASSWORD_SELECTOR}),
            description_placeholders={"url": self._discovered_url},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change Gateway URL and/or API key without removing the entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input.get(CONF_API_KEY) or entry.data[CONF_API_KEY]
            result = await self._async_validate(user_input[CONF_URL], api_key, errors)
            if result is not None:
                base_url, status = result
                await self.async_set_unique_id(status.instance_id)
                self._abort_if_unique_id_mismatch(reason="wrong_gateway")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_URL: base_url, CONF_API_KEY: api_key}
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_URL): URL_SELECTOR,
                        vol.Optional(CONF_API_KEY): PASSWORD_SELECTOR,
                    }
                ),
                {CONF_URL: (user_input or entry.data)[CONF_URL]},
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauthentication when the Gateway rejects the API key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new Gateway API key.

        This is only about the Gateway API key; an expired LINE session is
        fixed in the Gateway Web UI and never triggers this flow.
        """
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
