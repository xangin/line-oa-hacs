"""Config flow for the LINE OA integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import probatio as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    GatewayAuthError,
    GatewayResponseError,
    GatewayStatus,
    LineOAError,
    LineOAGatewayClient,
    normalize_base_url,
)
from .const import DEFAULT_NAME, DOMAIN, LOGGER

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


class LineOAConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for LINE OA."""

    VERSION = 1
    MINOR_VERSION = 1

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

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_URL): URL_SELECTOR,
                        vol.Required(CONF_API_KEY): PASSWORD_SELECTOR,
                    }
                ),
                user_input,
            ),
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
