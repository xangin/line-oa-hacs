"""Fixtures for LINE OA tests. All Gateway traffic goes to a local mock server."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator

from aiohttp.test_utils import TestServer
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.line_oa.const import DOMAIN
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant

from .mock_gateway import MockGatewayState, create_app


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Enable loading custom_components in every test."""
    return


@pytest.fixture
def gateway_state() -> MockGatewayState:
    """Return the mock Gateway state."""
    return MockGatewayState()


@pytest.fixture
async def gateway(
    gateway_state: MockGatewayState, socket_enabled: None
) -> AsyncGenerator[TestServer]:
    """Run the mock Gateway on 127.0.0.1 (HA tests only allow localhost)."""
    server = TestServer(create_app(gateway_state), host="127.0.0.1")
    await server.start_server()
    yield server
    await server.close()


@pytest.fixture
def gateway_url(gateway: TestServer) -> str:
    """Return the base URL of the mock Gateway."""
    return str(gateway.make_url("")).rstrip("/")


@pytest.fixture
def config_entry(gateway_url: str, gateway_state: MockGatewayState) -> MockConfigEntry:
    """Return a config entry pointing at the mock Gateway."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="LINE OA",
        unique_id=gateway_state.instance_id,
        data={CONF_URL: gateway_url, CONF_API_KEY: gateway_state.api_key},
    )


@pytest.fixture
async def setup_integration(hass: HomeAssistant, config_entry: MockConfigEntry) -> MockConfigEntry:
    """Set up the integration with the mock Gateway."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
