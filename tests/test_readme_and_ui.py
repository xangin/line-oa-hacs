"""Validate README YAML examples for real, and the action UI descriptions."""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.line_oa.const import DOMAIN
from custom_components.line_oa.images import LoadedImage
from homeassistant.components.camera.const import DATA_COMPONENT
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.script import Script, async_validate_actions_config
from homeassistant.helpers.service import async_get_all_descriptions
from homeassistant.helpers.translation import async_get_translations
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import parse_yaml

from .mock_gateway import MockGatewayState
from .test_services import JPEG, FakeCamera

README = Path(__file__).parent.parent / "README.md"


def _readme_action_blocks() -> list[str]:
    blocks = re.findall(r"```yaml\n(.*?)```", README.read_text(encoding="utf-8"), re.S)
    return [block for block in blocks if "action: line_oa." in block]


def test_readme_contains_all_three_actions() -> None:
    """The README documents each action with YAML."""
    text = "\n".join(_readme_action_blocks())
    for name in ("send_text", "send_image", "send_screen_shot"):
        assert f"action: line_oa.{name}" in text


@pytest.mark.parametrize("block", _readme_action_blocks())
async def test_readme_yaml_examples_run(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
    tmp_path: Path,
    block: str,
) -> None:
    """Each README example passes HA's script schema and runs against the mock Gateway."""
    config: Any = parse_yaml(block)
    # Automation style (``actions:`` list) or Developer Tools style (one action).
    raw_actions = config.get("actions", [config])
    actions = cv.SCRIPT_SCHEMA(raw_actions)
    gateway_state.aliases.append("home")  # alias used by the quick-start example
    actions = await async_validate_actions_config(hass, actions)

    # Environment the examples refer to.
    media = Path(os.path.realpath(tmp_path)) / "media"
    media.mkdir()
    for name in ("test.jpg", "snapshot.jpg"):
        (media / name).write_bytes(JPEG)
    hass.config.allowlist_external_dirs = {str(media)}
    hass.states.async_set("sensor.bathroom_temperature", "26.5")
    assert await async_setup_component(hass, "system_log", {})
    assert await async_setup_component(hass, "camera", {})
    await hass.data[DATA_COMPONENT].async_add_entities([FakeCamera("Front door", JPEG)])
    await hass.async_block_till_done()

    # The README uses /media; map it to the test media directory for execution.
    for action in actions:
        if "image_path" in action.get("data", {}):
            path = action["data"]["image_path"]
            if path.startswith(("http://", "https://")):
                continue
            assert path.startswith("/media/")
            action["data"]["image_path"] = str(media / path.removeprefix("/media/"))

    script = Script(hass, actions, "README example", DOMAIN)
    # Remote examples use synthetic image bytes; never contact the public example host.
    with patch(
        "custom_components.line_oa.services.async_download_image",
        AsyncMock(return_value=LoadedImage(JPEG, "image/jpeg", "remote.jpg")),
    ):
        await script.async_run(context=Context())
    await hass.async_block_till_done()

    line_oa_calls = [a for a in actions if a["action"].startswith("line_oa.")]
    assert len(gateway_state.requests) == len(line_oa_calls)
    for request in gateway_state.requests:
        assert request.fields["who"] in {"simon", "home"}
    if "bathroom_temperature" in block:
        assert "26.5°C" in gateway_state.requests[0].fields["message"]


async def test_actions_visible_in_ui_editor(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """services.yaml exposes the fields and selectors used by the automation editor."""
    descriptions = (await async_get_all_descriptions(hass))[DOMAIN]
    assert set(descriptions) == {"send_text", "send_image", "send_screen_shot"}

    text = descriptions["send_text"]["fields"]
    assert text["who"]["required"] is True
    assert text["who"]["selector"]["text"]["multiline"] is False
    assert text["who"]["selector"]["text"]["multiple"] is False
    assert text["message"]["selector"]["text"]["multiline"] is True

    image = descriptions["send_image"]["fields"]
    assert "text" in image["image_path"]["selector"]
    assert set(image) == {"who", "image_path"}

    shot = descriptions["send_screen_shot"]["fields"]
    entity_selector = shot["camera_entity"]["selector"]["entity"]
    assert entity_selector["filter"][0]["domain"] == ["camera"]
    assert entity_selector.get("multiple", False) is False
    assert "text" in shot["who"]["selector"]
    assert set(shot) == {"who", "camera_entity"}

    # Names/descriptions come from the translation files (checked below).
    for name in descriptions:
        assert descriptions[name]["response"] == {"optional": True}


async def test_traditional_chinese_ui_strings(hass: HomeAssistant) -> None:
    """Action, field and entity names are translated to Traditional Chinese."""
    services = await async_get_translations(hass, "zh-Hant", "services", [DOMAIN])
    prefix = f"component.{DOMAIN}.services"
    assert services[f"{prefix}.send_text.name"] == "傳送文字"
    assert services[f"{prefix}.send_image.name"] == "傳送圖片"
    assert services[f"{prefix}.send_screen_shot.name"] == "傳送攝影機截圖"
    assert services[f"{prefix}.send_text.fields.who.name"] == "接收者"
    assert services[f"{prefix}.send_text.fields.message.name"] == "訊息"
    assert services[f"{prefix}.send_image.fields.image_path.name"] == "圖片路徑或網址"
    assert services[f"{prefix}.send_screen_shot.fields.camera_entity.name"] == "攝影機"
    assert services[f"{prefix}.send_text.fields.who.description"].startswith(
        "接收者別名，請填寫 LINE OA Gateway 中已設定的別名。"
    )

    entities = await async_get_translations(hass, "zh-Hant", "entity", [DOMAIN])
    prefix = f"component.{DOMAIN}.entity"
    assert entities[f"{prefix}.binary_sensor.connected.name"] == "Gateway 連線狀態"
    assert entities[f"{prefix}.binary_sensor.logged_in.name"] == "LINE 登入狀態"
    assert entities[f"{prefix}.sensor.session_status.name"] == "LINE Session 狀態"
    assert entities[f"{prefix}.sensor.last_error.name"] == "最近錯誤"


async def test_entity_ids_stay_english_in_chinese_install(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """With HA set to zh-Hant, entity IDs still match the documented IDs."""
    hass.config.language = "zh-Hant"
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.line_oa_connected") is not None
    assert hass.states.get("binary_sensor.line_oa_logged_in") is not None
    assert hass.states.get("sensor.line_oa_session_status") is not None
    assert hass.states.get("sensor.line_oa_last_error") is not None
    assert (
        hass.states.get("binary_sensor.line_oa_connected").attributes["friendly_name"]
        == "LINE OA Gateway 連線狀態"
    )
