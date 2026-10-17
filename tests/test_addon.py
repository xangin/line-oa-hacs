"""Gateway app (add-on) mode: Supervisor discovery, secure pairing, switching.

The app is simulated by the mock Gateway (pairing behaves like the real
line-oa-ha-addon ``pairing.ts``) and a fake Supervisor discovery reader.
No real LINE message is ever sent.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from aiohasupervisor import SupervisorError
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.line_oa.addon import (
    AddonDiscoveryError,
    async_find_discovery,
    bootstrap_token,
    parse_discovery,
)
from custom_components.line_oa.api import LineOAGatewayClient, PairingError
from custom_components.line_oa.const import DOMAIN
from custom_components.line_oa.diagnostics import async_get_config_entry_diagnostics
from homeassistant.config_entries import SOURCE_HASSIO, SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .mock_gateway import MockGatewayState, create_app

DISCOVERY_UUID = "9f1c2b3a4d5e6f708192a3b4c5d6e7f8"
SLUG = "local_line_oa"
ENTITY_IDS = (
    "binary_sensor.line_oa_connected",
    "binary_sensor.line_oa_logged_in",
    "sensor.line_oa_session_status",
    "sensor.line_oa_last_error",
)


@dataclass
class AppEnv:
    """Controls the simulated app and Supervisor discovery."""

    state: MockGatewayState
    url: str
    announced: bool = True
    slug: str = SLUG

    def info(self) -> HassioServiceInfo:
        """Discovery message as Supervisor would hand it to HA Core."""
        return HassioServiceInfo(
            config=self.state.discovery_config(self.slug.replace("_", "-")),
            name="LINE OA",
            slug=self.slug,
            uuid=DISCOVERY_UUID,
        )

    async def find(
        self, hass: HomeAssistant, instance_id: str | None = None
    ) -> HassioServiceInfo | None:
        if not self.announced:
            return None
        info = self.info()
        if instance_id is not None and info.config["instance_id"] != instance_id:
            return None
        return info


@pytest.fixture
def app(gateway_state: MockGatewayState, gateway_url: str) -> Generator[AppEnv]:
    """An unpaired Gateway app reachable at the mock server."""
    gateway_state.paired = False
    gateway_state.api_version = 1
    gateway_state.api_key = "not-issued-yet"
    env = AppEnv(gateway_state, gateway_url)
    with (
        patch("custom_components.line_oa.addon.addon_url", return_value=gateway_url),
        patch("custom_components.line_oa.config_flow.async_find_discovery", side_effect=env.find),
        patch("custom_components.line_oa.config_flow.is_hassio", return_value=True),
    ):
        yield env


async def _discover(hass: HomeAssistant, app: AppEnv) -> dict[str, Any]:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_HASSIO}, data=app.info()
    )


async def _pair_new(hass: HomeAssistant, app: AppEnv) -> MockConfigEntry:
    result = await _discover(hass, app)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    return result["result"]


# --------------------------------------------------------------------------
# Discovery parsing (SSRF / identity)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("slug", ["local_line_oa", "a1b2c3d4_line_oa"])
def test_local_and_repository_hostname(slug: str) -> None:
    """The URL is built from the Supervisor slug, not trusted from the payload."""
    state = MockGatewayState(paired=False)
    info = HassioServiceInfo(
        config=state.discovery_config(slug.replace("_", "-")),
        name="LINE OA",
        slug=slug,
        uuid=DISCOVERY_UUID,
    )
    addon = parse_discovery(info)
    assert addon.url == f"http://{slug.replace('_', '-')}:8787"
    assert addon.instance_id == state.instance_id
    assert addon.discovery_uuid == DISCOVERY_UUID
    assert addon.pairing_required is True
    assert "bootstrap" not in repr(addon)
    assert state.bootstrap not in repr(addon)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("host", "192.168.1.1", "invalid_discovery_info"),
        ("host", "attacker.example", "invalid_discovery_info"),
        ("host", "local-line-oa/path", "invalid_discovery_info"),
        ("host", "local-line-oa:80", "invalid_discovery_info"),
        ("port", 8099, "invalid_discovery_info"),
        ("port", True, "invalid_discovery_info"),
        ("port", "8787", "invalid_discovery_info"),
        ("instance_id", "invalid", "invalid_discovery_info"),
        ("instance_id", None, "invalid_discovery_info"),
        ("api_version", 2, "incompatible_api"),
        ("api_version", True, "incompatible_api"),
        ("api_version", None, "incompatible_api"),
    ],
)
def test_invalid_discovery_rejected(field: str, value: object, error: str) -> None:
    """Forged or incompatible discovery data never produces a target URL."""
    config = MockGatewayState(paired=False).discovery_config()
    config[field] = value
    info = HassioServiceInfo(config=config, name="LINE OA", slug=SLUG, uuid=DISCOVERY_UUID)
    with pytest.raises(AddonDiscoveryError, match=error):
        parse_discovery(info)


@pytest.mark.parametrize("slug", ["other_addon", "local_line_oa_evil", "LOCAL_line_oa"])
def test_discovery_from_other_slug_rejected(slug: str) -> None:
    config = MockGatewayState().discovery_config(slug.replace("_", "-"))
    info = HassioServiceInfo(config=config, name="x", slug=slug, uuid=DISCOVERY_UUID)
    with pytest.raises(AddonDiscoveryError, match="invalid_discovery_info"):
        parse_discovery(info)


def test_bootstrap_token_validation() -> None:
    state = MockGatewayState(paired=False)
    info = HassioServiceInfo(
        config=state.discovery_config(), name="LINE OA", slug=SLUG, uuid=DISCOVERY_UUID
    )
    assert bootstrap_token(info) == state.bootstrap
    info.config["bootstrap_token"] = "short"
    assert bootstrap_token(info) is None
    state.expire_bootstrap()
    info.config["bootstrap_token"] = state.bootstrap
    info.config["bootstrap_expires_at"] = "2000-01-01T00:00:00+00:00"
    assert bootstrap_token(info) is None


async def test_find_discovery_reads_core_only_supervisor_api(hass: HomeAssistant) -> None:
    """Discovery is read through the Core Supervisor client, filtered by service/instance."""
    state = MockGatewayState(paired=False)
    record = SimpleNamespace(
        service=DOMAIN, addon=SLUG, uuid=UUID(DISCOVERY_UUID), config=state.discovery_config()
    )
    other = SimpleNamespace(
        service="mqtt", addon="core_mosquitto", uuid=uuid4(), config={"host": "x"}
    )
    discovery = SimpleNamespace(list=AsyncMock(return_value=[other, record]))
    with (
        patch("custom_components.line_oa.addon.is_hassio", return_value=True),
        patch(
            "custom_components.line_oa.addon._get_supervisor_client",
            return_value=SimpleNamespace(discovery=discovery),
        ),
    ):
        info = await async_find_discovery(hass, state.instance_id)
        assert info is not None
        assert info.slug == SLUG
        assert info.uuid == DISCOVERY_UUID
        assert info.config == record.config
        assert await async_find_discovery(hass, str(uuid4())) is None
        discovery.list.side_effect = SupervisorError("offline")
        with pytest.raises(AddonDiscoveryError, match="cannot_connect"):
            await async_find_discovery(hass)
    with patch("custom_components.line_oa.addon.is_hassio", return_value=False):
        assert await async_find_discovery(hass) is None


# --------------------------------------------------------------------------
# Discovery -> confirmation -> pairing
# --------------------------------------------------------------------------


async def test_discovery_shows_confirmation_and_pairs_after_consent(
    hass: HomeAssistant, app: AppEnv, gateway_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Nothing is paired or created until the user confirms."""
    caplog.set_level(logging.DEBUG)
    token = app.state.discovery_config()["bootstrap_token"]
    result = await _discover(hass, app)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "hassio_confirm"
    assert not result["data_schema"].schema
    assert result["description_placeholders"] == {"addon": "LINE OA"}
    assert app.state.pairing_calls == 0
    assert not hass.config_entries.async_entries(DOMAIN)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flows[0]["context"]["title_placeholders"] == {"name": "LINE OA"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.source == SOURCE_HASSIO
    assert entry.unique_id == app.state.instance_id
    assert entry.unique_id != DISCOVERY_UUID
    assert entry.data == {
        CONF_URL: gateway_url,
        CONF_API_KEY: app.state.api_key,
        "connection_mode": "addon",
        "instance_id": app.state.instance_id,
        "addon_slug": SLUG,
        "discovery_uuid": DISCOVERY_UUID,
    }
    assert app.state.api_key.startswith("loagw_")
    assert app.state.pairing_calls == 1
    for entity_id in ENTITY_IDS:
        assert hass.states.get(entity_id) is not None
    assert hass.states.get("binary_sensor.line_oa_connected").state == "on"
    assert hass.states.get("binary_sensor.line_oa_logged_in").state == "on"
    # Secrets never reach logs or states; the token never reaches the entry.
    # (HA strips "data" from create-entry results before they reach the frontend.)
    assert token not in repr(result)
    for secret in (token, app.state.api_key):
        assert secret not in caplog.text
        for state in hass.states.async_all():
            assert secret not in str(state.attributes)
            assert secret not in state.state


async def test_user_cancels_discovery(hass: HomeAssistant, app: AppEnv) -> None:
    """Closing the confirmation does not pair or create anything."""
    result = await _discover(hass, app)
    hass.config_entries.flow.async_abort(result["flow_id"])
    assert app.state.pairing_calls == 0
    assert not hass.config_entries.async_entries(DOMAIN)
    assert app.state.paired is False


async def test_confirmation_refreshes_rotated_token(hass: HomeAssistant, app: AppEnv) -> None:
    """The token from the original announcement may expire; a fresh one is read."""
    result = await _discover(hass, app)
    app.state.expire_bootstrap()  # the app rotates its token
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_rediscovery_restart_and_hostname_change_never_duplicate(
    hass: HomeAssistant, app: AppEnv
) -> None:
    """Republish / app restart / HA restart / new hostname: same entry, no re-pair."""
    entry = await _pair_new(hass, app)
    key = entry.data[CONF_API_KEY]

    # Republished message (e.g. every 30 s, or after an app restart).
    result = await _discover(hass, app)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"

    # Repository install changes the Supervisor hostname.
    app.slug = "a1b2c3d4_line_oa"
    with patch(
        "custom_components.line_oa.addon.addon_url", side_effect=lambda h: f"http://{h}:8787"
    ):
        result = await _discover(hass, app)
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_URL] == "http://a1b2c3d4-line-oa:8787"
    assert entry.data["addon_slug"] == "a1b2c3d4_line_oa"
    assert entry.data[CONF_API_KEY] == key
    assert app.state.pairing_calls == 1
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_ha_restart_with_paired_app(hass: HomeAssistant, app: AppEnv) -> None:
    """A stored app entry loads from storage without pairing again."""
    entry = await _pair_new(hass, app)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.line_oa_session_status").state == "ready"
    assert app.state.pairing_calls == 1


@pytest.mark.parametrize(
    ("prepare", "error", "calls"),
    [
        (lambda s: setattr(s, "bootstrap", "x" * 43), "pairing_rejected", 1),
        (lambda s: setattr(s, "pairing_status_override", 429), "pairing_rate_limited", 1),
        (lambda s: setattr(s, "pairing_status_override", 404), "pairing_unavailable", 1),
        (lambda s: setattr(s, "pairing_status_override", 500), "pairing_failed", 1),
    ],
)
async def test_pairing_errors_are_distinct_and_retryable(
    hass: HomeAssistant, app: AppEnv, prepare: Any, error: str, calls: int
) -> None:
    """Invalid token, rate limit, missing endpoint, server error, already paired."""
    result = await _discover(hass, app)
    real_info = app.info

    def stale_info() -> HassioServiceInfo:
        info = real_info()
        prepare(app.state)
        return info

    app.info = stale_info  # type: ignore[method-assign]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert app.state.pairing_calls == calls
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_already_paired_app_asks_for_reset(hass: HomeAssistant, app: AppEnv) -> None:
    """A paired app publishes no token; the user is told to allow pairing in the app."""
    result = await _discover(hass, app)
    app.state.paired = True
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "addon_already_paired"}
    assert app.state.pairing_calls == 0


async def test_expired_token_is_not_sent(hass: HomeAssistant, app: AppEnv) -> None:
    result = await _discover(hass, app)
    real_info = app.info

    def expired_info() -> HassioServiceInfo:
        info = real_info()
        info.config["bootstrap_expires_at"] = "2000-01-01T00:00:00+00:00"
        return info

    app.info = expired_info  # type: ignore[method-assign]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "pairing_expired"}
    assert app.state.pairing_calls == 0


async def test_used_token_cannot_be_replayed(
    hass: HomeAssistant, app: AppEnv, gateway_url: str
) -> None:
    """A bootstrap token works once; a replay is rejected and issues no key."""
    token = app.state.discovery_config()["bootstrap_token"]
    client = LineOAGatewayClient(async_get_clientsession(hass), gateway_url, "")
    first = await client.async_pairing_exchange(app.state.instance_id, token)
    assert first.api_key == app.state.api_key
    assert first.api_key not in repr(first)
    with pytest.raises(PairingError):
        await client.async_pairing_exchange(app.state.instance_id, token)
    assert app.state.api_key == first.api_key


async def test_app_not_reachable_during_pairing(hass: HomeAssistant, app: AppEnv) -> None:
    result = await _discover(hass, app)
    with patch("custom_components.line_oa.addon.addon_url", return_value="http://127.0.0.1:9"):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "pairing_unavailable"}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_app_disappears_before_confirmation(hass: HomeAssistant, app: AppEnv) -> None:
    result = await _discover(hass, app)
    app.announced = False
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "addon_not_found"}


@pytest.mark.parametrize("outcome", ["redirect", "invalid", "wrong_instance", "huge"])
async def test_pairing_http_exchange_rejects_bad_responses(
    hass: HomeAssistant, socket_enabled: None, outcome: str
) -> None:
    """Redirects are not followed and malformed credentials are refused."""
    followed = False

    async def exchange(request: web.Request) -> web.StreamResponse:
        if outcome == "redirect":
            raise web.HTTPTemporaryRedirect("/elsewhere")
        if outcome == "huge":
            return web.json_response({"api_key": "loagw_" + "a" * 5000}, status=201)
        if outcome == "wrong_instance":
            return web.json_response(
                {"instance_id": str(uuid4()), "api_key": "loagw_" + "a" * 43, "api_version": 1},
                status=201,
            )
        return web.json_response({"instance_id": "x", "api_key": 1}, status=201)

    async def elsewhere(request: web.Request) -> web.StreamResponse:
        nonlocal followed
        followed = True
        return web.json_response({}, status=201)

    web_app = web.Application()
    web_app.router.add_post("/api/v1/pairing/exchange", exchange)
    web_app.router.add_post("/elsewhere", elsewhere)
    server = TestServer(web_app, host="127.0.0.1")
    await server.start_server()
    try:
        client = LineOAGatewayClient(
            async_get_clientsession(hass), str(server.make_url("")).rstrip("/"), ""
        )
        with pytest.raises(PairingError) as exc_info:
            await client.async_pairing_exchange(str(uuid4()), "t" * 43)
        assert "t" * 43 not in str(exc_info.value)
    finally:
        await server.close()
    assert followed is False


# --------------------------------------------------------------------------
# "Add integration" menu
# --------------------------------------------------------------------------


async def test_add_integration_goes_straight_to_addon(hass: HomeAssistant, app: AppEnv) -> None:
    """ "Add integration" uses the announced add-on directly (no mode menu)."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "hassio_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["connection_mode"] == "addon"


async def test_add_integration_without_addon_is_honest(hass: HomeAssistant, app: AppEnv) -> None:
    app.announced = False
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "addon_not_found"


async def test_add_integration_without_supervisor(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["reason"] == "addon_not_supported"


async def test_only_one_gateway(hass: HomeAssistant, app: AppEnv) -> None:
    """With an active Gateway, manual add / zeroconf do not create a second one."""
    await _pair_new(hass, app)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["reason"] == "single_instance_allowed"


# --------------------------------------------------------------------------
# External (LXC) -> app switch
# --------------------------------------------------------------------------


@pytest.fixture
async def lxc_entry(
    hass: HomeAssistant, gateway: TestServer, gateway_state: MockGatewayState
) -> MockConfigEntry:
    """An existing external (LXC) entry on a *different* Gateway instance."""
    lxc_state = MockGatewayState(instance_id="aaaaaaaa-0000-4000-8000-000000000001")
    server = TestServer(create_app(lxc_state), host="127.0.0.1")
    await server.start_server()
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=lxc_state.instance_id,
        data={
            CONF_URL: str(server.make_url("")).rstrip("/"),
            CONF_API_KEY: lxc_state.api_key,
            "connection_mode": "external",
            "instance_id": lxc_state.instance_id,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.lxc_state = lxc_state  # type: ignore[attr-defined]
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await server.close()


async def test_found_app_offers_keep_or_switch(
    hass: HomeAssistant, app: AppEnv, lxc_entry: MockConfigEntry
) -> None:
    """Never switches automatically; offers keep / switch."""
    old = dict(lxc_entry.data)
    result = await _discover(hass, app)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "hassio_migrate"
    assert result["menu_options"] == ["migrate_keep", "migrate_switch"]
    assert dict(lxc_entry.data) == old
    assert app.state.pairing_calls == 0


async def test_keep_current_stops_prompting(
    hass: HomeAssistant, app: AppEnv, lxc_entry: MockConfigEntry
) -> None:
    old_key = lxc_entry.data[CONF_API_KEY]
    result = await _discover(hass, app)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "migrate_keep"}
    )
    assert result["reason"] == "kept_current"
    assert lxc_entry.data["connection_mode"] == "external"
    assert lxc_entry.data[CONF_API_KEY] == old_key
    # The app republishes (token rotation): no new prompt.
    app.state.expire_bootstrap()
    result = await _discover(hass, app)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "addon_declined"
    assert app.state.pairing_calls == 0


async def test_switch_to_app_keeps_entities_and_actions(
    hass: HomeAssistant, app: AppEnv, lxc_entry: MockConfigEntry, gateway_url: str
) -> None:
    registry = er.async_get(hass)
    entity_registry_entry = registry.async_get("sensor.line_oa_session_status")
    assert entity_registry_entry is not None
    registry.async_update_entity("sensor.line_oa_session_status", name="我的 LINE 狀態")
    before = {
        e.entity_id: e.unique_id
        for e in er.async_entries_for_config_entry(registry, lxc_entry.entry_id)
    }
    old_data = dict(lxc_entry.data)

    result = await _discover(hass, app)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "migrate_switch"}
    )
    assert result["step_id"] == "migrate_switch"
    assert result["description_placeholders"]["old_instance"] == old_data["instance_id"]
    assert result["description_placeholders"]["new_instance"] == app.state.instance_id
    assert app.state.pairing_calls == 0
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert result["reason"] == "migration_successful"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert lxc_entry.unique_id == app.state.instance_id
    assert lxc_entry.data["connection_mode"] == "addon"
    assert lxc_entry.data[CONF_URL] == gateway_url
    assert lxc_entry.data["entity_instance_id"] == old_data["instance_id"]
    assert lxc_entry.data["previous_connection"] == {
        "connection_mode": "external",
        CONF_URL: old_data[CONF_URL],
        CONF_API_KEY: old_data[CONF_API_KEY],
        "instance_id": old_data["instance_id"],
    }
    after = {
        e.entity_id: e.unique_id
        for e in er.async_entries_for_config_entry(registry, lxc_entry.entry_id)
    }
    assert before == after
    assert registry.async_get("sensor.line_oa_session_status").name == "我的 LINE 狀態"
    assert hass.states.get("binary_sensor.line_oa_connected").state == "on"

    # Existing automations keep working: actions now go to the app.
    await hass.services.async_call(
        DOMAIN, "send_text", {"who": "simon", "message": "x"}, blocking=True
    )
    assert len(app.state.requests) == 1
    assert lxc_entry.lxc_state.requests == []  # type: ignore[attr-defined]


async def test_failed_pairing_leaves_lxc_untouched(
    hass: HomeAssistant, app: AppEnv, lxc_entry: MockConfigEntry
) -> None:
    old = dict(lxc_entry.data)
    old_unique = lxc_entry.unique_id
    result = await _discover(hass, app)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "migrate_switch"}
    )
    app.state.pairing_status_override = 500
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "pairing_failed"}
    assert dict(lxc_entry.data) == old
    assert lxc_entry.unique_id == old_unique
    assert hass.states.get("binary_sensor.line_oa_connected").state == "on"


async def test_switch_back_to_lxc_via_reconfigure(
    hass: HomeAssistant, app: AppEnv, lxc_entry: MockConfigEntry
) -> None:
    """The previous LXC settings are offered for rollback."""
    old = dict(lxc_entry.data)
    result = await _discover(hass, app)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "migrate_switch"}
    )
    await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    result = await lxc_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["reconfigure_addon", "reconfigure_external"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "reconfigure_external"}
    )
    schema = {str(k): k for k in result["data_schema"].schema}
    assert schema[CONF_URL].description == {"suggested_value": old[CONF_URL]}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: old[CONF_URL]}
    )
    assert result["errors"] == {CONF_API_KEY: "api_key_required"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: old[CONF_URL], CONF_API_KEY: old[CONF_API_KEY]}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert lxc_entry.data["connection_mode"] == "external"
    assert lxc_entry.unique_id == old["instance_id"]
    assert lxc_entry.data["entity_instance_id"] == old["instance_id"]
    assert hass.states.get("sensor.line_oa_session_status").state == "ready"


async def test_external_reconfigure_offers_addon_on_supervisor(
    hass: HomeAssistant, app: AppEnv, lxc_entry: MockConfigEntry
) -> None:
    result = await lxc_entry.start_reconfigure_flow(hass)
    assert result["menu_options"] == ["reconfigure_external", "reconfigure_addon"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "reconfigure_addon"}
    )
    assert result["step_id"] == "reconfigure_addon"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert lxc_entry.data["connection_mode"] == "addon"


def test_app_removal_never_matches_our_entries() -> None:
    """HA Core removes only hassio entries whose unique_id equals the discovery UUID.

    Our unique_id is the Gateway instance_id (UUID with dashes), the discovery
    UUID is hex: the two can never be equal, so uninstalling the app never
    deletes a LINE OA entry (app mode or switched back to external).
    """
    instance_id = MockGatewayState().instance_id
    assert str(UUID(instance_id)) == instance_id
    assert UUID(DISCOVERY_UUID).hex == DISCOVERY_UUID
    assert instance_id != DISCOVERY_UUID
    assert "-" in instance_id
    assert "-" not in DISCOVERY_UUID


# --------------------------------------------------------------------------
# Re-pairing (API key invalid) vs. LINE session
# --------------------------------------------------------------------------


async def test_revoked_key_starts_secure_repairing(
    hass: HomeAssistant, app: AppEnv, caplog: pytest.LogCaptureFixture
) -> None:
    """After "Allow Home Assistant pairing" in the app, reauth re-pairs without typing a key."""
    caplog.set_level(logging.DEBUG)
    entry = await _pair_new(hass, app)
    old_key = entry.data[CONF_API_KEY]
    app.state.reset_pairing()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.line_oa_connected").state == "on"
    assert hass.states.get("binary_sensor.line_oa_logged_in").state == "unavailable"
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]
    result = await hass.config_entries.flow.async_configure(flows[0]["flow_id"])
    assert result["step_id"] == "reauth_addon"
    assert not result["data_schema"].schema

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == app.state.api_key != old_key
    assert hass.states.get("binary_sensor.line_oa_logged_in").state == "on"
    assert app.state.api_key not in caplog.text


async def test_reauth_before_reset_explains_what_to_do(hass: HomeAssistant, app: AppEnv) -> None:
    entry = await _pair_new(hass, app)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reauth", "entry_id": entry.entry_id}, data=entry.data
    )
    assert result["step_id"] == "reauth_addon"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "addon_already_paired"}


async def test_reconfigure_app_repair(hass: HomeAssistant, app: AppEnv) -> None:
    entry = await _pair_new(hass, app)
    app.state.reset_pairing()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    assert result["menu_options"] == ["reconfigure_addon", "reconfigure_external"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "reconfigure_addon"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_API_KEY] == app.state.api_key


async def test_line_session_expired_is_not_a_pairing_problem(
    hass: HomeAssistant, app: AppEnv
) -> None:
    entry = await _pair_new(hass, app)
    app.state.line_logged_in = False
    app.state.session_status = "auth_required"
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.line_oa_connected").state == "on"
    assert hass.states.get("binary_sensor.line_oa_logged_in").state == "off"
    assert hass.states.get("sensor.line_oa_session_status").state == "auth_required"
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_app_offline_keeps_credential_and_entry(hass: HomeAssistant, app: AppEnv) -> None:
    entry = await _pair_new(hass, app)
    key = entry.data[CONF_API_KEY]
    app.state.health_status = 503
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.line_oa_connected").state == "off"
    assert hass.states.get("binary_sensor.line_oa_logged_in").state == "unavailable"
    assert hass.states.get("sensor.line_oa_session_status").state == "unavailable"
    assert entry.data[CONF_API_KEY] == key
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_different_gateway_at_same_address_is_not_trusted(
    hass: HomeAssistant, app: AppEnv
) -> None:
    """If the app is reinstalled (new instance_id), its state is not shown as ours."""
    entry = await _pair_new(hass, app)
    app.state.instance_id = str(uuid4())
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.line_oa_connected").state == "on"
    assert hass.states.get("sensor.line_oa_session_status").state == "unavailable"


async def test_diagnostics_redact_credentials(hass: HomeAssistant, app: AppEnv) -> None:
    entry = await _pair_new(hass, app)
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert app.state.api_key not in str(diagnostics)
    assert diagnostics["connection_mode"] == "addon"
    assert diagnostics["entry_data"][CONF_API_KEY] == "**REDACTED**"
    assert diagnostics["status"]["api_version"] == 1


async def test_legacy_entry_without_connection_mode_is_external(
    hass: HomeAssistant, config_entry: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """Entries without connection_mode (created before app support) work as external."""
    assert "connection_mode" not in config_entry.data
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.line_oa_connected").state == "on"
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure_external"
