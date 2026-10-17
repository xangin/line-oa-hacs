"""Tests for line_oa.send_text, line_oa.send_image and line_oa.send_screen_shot."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch
import uuid

import probatio as vol
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.line_oa.const import DOMAIN
from homeassistant.components.camera import Camera
from homeassistant.components.camera.const import DATA_COMPONENT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.translation import async_get_translations
from homeassistant.setup import async_setup_component

from .mock_gateway import MockGatewayState

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 64 + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + b"\x00" * 32


async def _call(
    hass: HomeAssistant, service: str, data: dict[str, Any], *, response: bool = False
) -> Any:
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, return_response=response
    )


# --------------------------------------------------------------------------
# send_text
# --------------------------------------------------------------------------


async def test_send_text(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """send_text posts who/message/request_id once and returns the result."""
    result = await _call(
        hass, "send_text", {"who": "simon", "message": "洗衣機已完成"}, response=True
    )
    assert len(gateway_state.requests) == 1
    request = gateway_state.requests[0]
    assert request.path == "/api/v1/messages/text"
    assert set(request.fields) == {"who", "message", "request_id"}
    assert request.fields["who"] == "simon"
    assert request.fields["message"] == "洗衣機已完成"
    uuid.UUID(request.fields["request_id"])
    assert result == {
        "status": "submitted",
        "request_id": request.fields["request_id"],
        "message_id": "m-1",
        "who": "simon",
    }


async def test_send_text_without_response_data(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """The action also works as a plain (no response) call."""
    assert await _call(hass, "send_text", {"who": "family", "message": "hi"}) is None
    assert len(gateway_state.requests) == 1


async def test_send_text_confirmed(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """A confirmed result is reported as confirmed, not upgraded/downgraded."""
    gateway_state.send_status = "confirmed"
    result = await _call(hass, "send_text", {"who": "simon", "message": "x"}, response=True)
    assert result["status"] == "confirmed"


async def test_send_text_template_message(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """Templates in message are rendered by the script engine before sending."""
    hass.states.async_set("sensor.bathroom_temperature", "26.5")
    assert await async_setup_component(
        hass,
        "script",
        {
            "script": {
                "notify_temp": {
                    "sequence": [
                        {
                            "action": "line_oa.send_text",
                            "data": {
                                "who": "simon",
                                "message": "浴室溫度：\n{{ states('sensor.bathroom_temperature') }}°C",
                            },
                        }
                    ]
                }
            }
        },
    )
    await hass.services.async_call("script", "notify_temp", blocking=True)
    assert gateway_state.requests[0].fields["message"] == "浴室溫度：\n26.5°C"


@pytest.mark.parametrize(
    "data",
    [
        {"who": ["simon", "family"], "message": "x"},
        {"who": [], "message": "x"},
        {"who": "", "message": "x"},
        {"who": "   ", "message": "x"},
        {"who": {"alias": "simon"}, "message": "x"},
        {"message": "x"},
        {"who": "simon"},
        {"who": "simon", "message": "   "},
        {"who": "simon", "message": "x" * 5001},
        {"recipient": "simon", "message": "x"},
        {"target": "simon", "message": "x"},
        {"chat_id": "U123", "message": "x"},
        {"who": "simon", "message": "x", "chat_id": "U123"},
    ],
)
async def test_send_text_invalid_data(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
    data: dict[str, Any],
) -> None:
    """who must be one string; recipient/target/chat_id are not accepted."""
    with pytest.raises(vol.Invalid):
        await _call(hass, "send_text", data)
    assert gateway_state.requests == []


async def test_who_list_error_message_is_clear(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The error for a list explains the single-alias / LINE group rule."""
    with pytest.raises(vol.Invalid, match="single alias"):
        await _call(hass, "send_text", {"who": ["simon", "family"], "message": "x"})


async def test_unknown_alias_is_rejected_before_sending(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """An unknown alias never falls back to another recipient."""
    with pytest.raises(ServiceValidationError) as exc_info:
        await _call(hass, "send_text", {"who": "nobody", "message": "x"})
    assert exc_info.value.translation_key == "alias_not_found"
    assert exc_info.value.translation_placeholders == {"who": "nobody"}
    assert gateway_state.requests == []


async def test_alias_not_found_from_gateway(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """Without an aliases endpoint, the Gateway's ALIAS_NOT_FOUND is still mapped."""
    gateway_state.aliases_status = 404
    with pytest.raises(ServiceValidationError) as exc_info:
        await _call(hass, "send_text", {"who": "nobody", "message": "x"})
    assert exc_info.value.translation_key == "alias_not_found"
    assert len(gateway_state.requests) == 1


async def test_session_expired_on_send(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """SESSION_EXPIRED is reported clearly and refreshes status from the Gateway."""
    gateway_state.line_logged_in = False
    gateway_state.session_status = "expired"
    with pytest.raises(HomeAssistantError) as exc_info:
        await _call(hass, "send_text", {"who": "simon", "message": "x"})
    assert exc_info.value.translation_key == "line_session_expired"
    await hass.async_block_till_done(wait_background_tasks=True)
    await asyncio.sleep(0)
    await setup_integration.runtime_data.async_refresh()
    assert hass.states.get("binary_sensor.line_oa_logged_in").state == "off"
    # No reauth for the HA API key because of a LINE session problem.
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_line_rejection_does_not_mark_logged_out(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """A LINE rejection is not interpreted as being logged out."""
    gateway_state.send_response = (
        200,
        {"status": "failed", "error": {"code": "LINE_REJECTED", "message": "Blocked by user"}},
    )
    with pytest.raises(HomeAssistantError) as exc_info:
        await _call(hass, "send_text", {"who": "simon", "message": "x"})
    assert exc_info.value.translation_key == "line_rejected"
    assert exc_info.value.translation_placeholders == {"reason": "Blocked by user"}
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("binary_sensor.line_oa_logged_in").state == "on"


async def test_gateway_unreachable_on_send(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway: Any
) -> None:
    """Connection refused: the message was not sent; error says so."""
    await gateway.close()
    with pytest.raises(HomeAssistantError) as exc_info:
        await _call(hass, "send_text", {"who": "simon", "message": "x"})
    assert exc_info.value.translation_key == "cannot_connect"


async def test_api_key_rejected_on_send(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """A rejected API key is reported and starts reauth."""
    gateway_state.api_key = "rotated"
    with pytest.raises(HomeAssistantError) as exc_info:
        await _call(hass, "send_text", {"who": "simon", "message": "x"})
    assert exc_info.value.translation_key == "invalid_api_key"
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_send_timeout_is_unknown_and_not_retried(
    hass: HomeAssistant, setup_integration: MockConfigEntry, gateway_state: MockGatewayState
) -> None:
    """A POST timeout reports an unknown result and is never resent."""
    gateway_state.send_delay = 1.0
    with (
        patch("custom_components.line_oa.api.TEXT_POST_TIMEOUT", 0.2),
        pytest.raises(HomeAssistantError) as exc_info,
    ):
        await _call(hass, "send_text", {"who": "simon", "message": "x"})
    assert exc_info.value.translation_key == "send_unknown"
    request_id = gateway_state.requests[0].fields["request_id"]
    assert exc_info.value.translation_placeholders == {"request_id": request_id}
    await asyncio.sleep(1.2)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(gateway_state.requests) == 1


@pytest.mark.parametrize(
    ("response", "expected_key"),
    [
        ((200, {"status": "unknown", "request_id": "r"}), "send_unknown"),
        ((202, {"status": "unknown"}), "send_unknown"),
        ((200, {"status": "failed", "error": {"code": "SEND_UNKNOWN"}}), "send_unknown"),
        ((409, {"error": {"code": "SEND_UNKNOWN", "message": "?"}}), "send_unknown"),
        ((200, {"ok": True}), "send_unknown"),
        ((200, b"not json"), "send_unknown"),
        ((500, {"error": "boom"}), "send_unknown"),
        ((502, b"Bad gateway"), "send_unknown"),
        ((504, b""), "send_unknown"),
        ((503, b""), "gateway_unavailable"),
        ((429, {"error": {"code": "RATE_LIMITED", "message": "slow down"}}), "rate_limited"),
        ((413, b""), "gateway_payload_too_large"),
        (
            (400, {"error": {"code": "INVALID_REQUEST", "message": "bad"}}),
            "gateway_rejected_request",
        ),
        ((409, {"error": {"code": "AUTH_REQUIRED"}}), "line_auth_required"),
        ((409, {"error": {"code": "SESSION_EXPIRED"}}), "line_session_expired"),
        ((200, {"status": "failed"}), "line_rejected"),
        ((404, b""), "endpoint_not_found"),
    ],
)
async def test_send_result_mapping(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
    response: tuple[int, Any],
    expected_key: str,
) -> None:
    """Gateway outcomes map to distinct HA errors; ambiguous ones are "unknown"."""
    gateway_state.send_response = response
    with pytest.raises(HomeAssistantError) as exc_info:
        await _call(hass, "send_text", {"who": "simon", "message": "x"})
    assert exc_info.value.translation_key == expected_key
    assert len(gateway_state.requests) == 1


async def test_unknown_error_message_in_traditional_chinese(hass: HomeAssistant) -> None:
    """The zh-Hant message for unknown results uses the required wording."""
    translations = await async_get_translations(hass, "zh-Hant", "exceptions", [DOMAIN])
    message = translations[f"component.{DOMAIN}.exceptions.send_unknown.message"]
    assert message.startswith("LINE 訊息發送結果不明，可能已經送出，請勿立即重試。")


# --------------------------------------------------------------------------
# send_image
# --------------------------------------------------------------------------


@pytest.fixture
def media_dir(hass: HomeAssistant, tmp_path: Path) -> Path:
    """Create an allowlisted media directory."""
    media = tmp_path / "media"
    media.mkdir()
    real = Path(os.path.realpath(media))
    hass.config.allowlist_external_dirs = {str(real)}
    return real


async def test_send_image_jpeg_with_message(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
    media_dir: Path,
) -> None:
    """The file is read by HA and uploaded as multipart (not passed as a path)."""
    image = media_dir / "snapshot.jpg"
    image.write_bytes(JPEG)
    result = await _call(
        hass,
        "send_image",
        {"who": "simon", "image_path": str(image), "message": "設備檢查結果"},
        response=True,
    )
    assert result["status"] == "submitted"
    request = gateway_state.requests[0]
    assert request.path == "/api/v1/messages/image"
    assert request.image == JPEG
    assert request.image_content_type == "image/jpeg"
    assert request.image_filename == "snapshot.jpg"
    assert set(request.fields) == {"who", "message", "request_id"}
    assert request.fields["message"] == "設備檢查結果"
    assert str(image) not in str(request.fields)


async def test_send_image_png_without_message(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
    media_dir: Path,
) -> None:
    """PNG works; an omitted message is not sent as a field."""
    image = media_dir / "chart.png"
    image.write_bytes(PNG)
    await _call(hass, "send_image", {"who": "simon", "image_path": str(image)})
    request = gateway_state.requests[0]
    assert request.image_content_type == "image/png"
    assert set(request.fields) == {"who", "request_id"}


async def test_send_image_message_not_supported_by_gateway(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
    media_dir: Path,
) -> None:
    """If the Gateway does not support message, this is reported, not ignored."""
    gateway_state.supports_image_message = False
    image = media_dir / "snapshot.jpg"
    image.write_bytes(JPEG)
    with pytest.raises(HomeAssistantError) as exc_info:
        await _call(hass, "send_image", {"who": "simon", "image_path": str(image), "message": "x"})
    assert exc_info.value.translation_key == "message_not_supported"


async def test_send_image_errors(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
    media_dir: Path,
    tmp_path: Path,
) -> None:
    """Missing, disallowed, malformed and oversized files never reach the Gateway."""
    # Use a private config dir (inside a hidden dir, like Core's ~/.homeassistant).
    config_dir = Path(os.path.realpath(tmp_path)) / ".homeassistant"
    config_dir.mkdir()
    hass.config.config_dir = str(config_dir)
    (config_dir / ".storage").mkdir(exist_ok=True)
    (config_dir / ".storage" / "auth.jpg").write_bytes(JPEG)
    (config_dir / "secrets.yaml").write_text("api_key: hunter2\n")
    (config_dir / "www").mkdir(exist_ok=True)
    (config_dir / "www" / "ok.jpg").write_bytes(JPEG)
    # Even if the user allowlists the whole config dir, secrets stay blocked.
    hass.config.allowlist_external_dirs = {str(media_dir), str(config_dir)}

    outside = Path(os.path.realpath(tmp_path)) / "outside.jpg"
    outside.write_bytes(JPEG)
    (media_dir / "link_to_secrets.jpg").symlink_to(config_dir / "secrets.yaml")
    (media_dir / "link_outside.jpg").symlink_to(outside)
    (media_dir / "link_storage.jpg").symlink_to(config_dir / ".storage" / "auth.jpg")
    (media_dir / "text.jpg").write_text("not an image")
    (media_dir / "anim.gif").write_bytes(b"GIF89a" + b"\x00" * 20)
    (media_dir / "folder.jpg").mkdir()
    (media_dir / "big.jpg").write_bytes(JPEG[:4] + b"\x00" * (10 * 1024 * 1024))
    (media_dir / "exact.jpg").write_bytes(JPEG + b"\x00" * (10 * 1024 * 1024 - len(JPEG)))

    cases = {
        str(media_dir / "missing.jpg"): "image_not_found",
        "media/snapshot.jpg": "image_path_not_absolute",
        str(outside): "image_path_not_allowed",
        str(media_dir / ".." / "outside.jpg"): "image_path_not_allowed",
        str(config_dir / "secrets.yaml"): "image_path_not_allowed",
        str(config_dir / ".storage" / "auth.jpg"): "image_path_not_allowed",
        str(media_dir / "link_to_secrets.jpg"): "image_path_not_allowed",
        str(media_dir / "link_outside.jpg"): "image_path_not_allowed",
        str(media_dir / "link_storage.jpg"): "image_path_not_allowed",
        "/etc/passwd": "image_path_not_allowed",
        str(media_dir / "text.jpg"): "image_invalid_format",
        str(media_dir / "anim.gif"): "image_invalid_format",
        str(media_dir / "folder.jpg"): "image_not_found",
        str(media_dir / "big.jpg"): "image_too_large",
    }
    for path, expected in cases.items():
        with pytest.raises(ServiceValidationError) as exc_info:
            await _call(hass, "send_image", {"who": "simon", "image_path": path})
        assert exc_info.value.translation_key == expected, path
    assert gateway_state.requests == []

    # Exactly 10 MiB is accepted; www (default allowlist) is readable.
    await _call(hass, "send_image", {"who": "simon", "image_path": str(media_dir / "exact.jpg")})
    await _call(
        hass, "send_image", {"who": "simon", "image_path": str(config_dir / "www" / "ok.jpg")}
    )
    assert len(gateway_state.requests) == 2


async def test_send_image_unknown_alias_skips_file_read(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
    media_dir: Path,
) -> None:
    """who is validated before the image is read."""
    with (
        patch("custom_components.line_oa.services.async_load_local_image") as load,
        pytest.raises(ServiceValidationError),
    ):
        await _call(hass, "send_image", {"who": "nobody", "image_path": str(media_dir / "a.jpg")})
    load.assert_not_called()


async def test_send_image_timeout_is_unknown(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
    media_dir: Path,
) -> None:
    """Image upload timeouts are also "unknown" and not retried."""
    image = media_dir / "snapshot.jpg"
    image.write_bytes(JPEG)
    gateway_state.send_delay = 1.0
    with (
        patch("custom_components.line_oa.api.IMAGE_POST_TIMEOUT", 0.2),
        pytest.raises(HomeAssistantError) as exc_info,
    ):
        await _call(hass, "send_image", {"who": "simon", "image_path": str(image)})
    assert exc_info.value.translation_key == "send_unknown"
    await asyncio.sleep(1.2)
    assert len(gateway_state.requests) == 1


# --------------------------------------------------------------------------
# send_screen_shot
# --------------------------------------------------------------------------


class FakeCamera(Camera):
    """Test camera returning a configurable still image."""

    def __init__(
        self,
        name: str,
        image: bytes | Exception | None,
        *,
        available: bool = True,
        is_on: bool = True,
        content_type: str = "image/jpeg",
        stream_only: bool = False,
    ) -> None:
        super().__init__()
        self._attr_name = name
        self._attr_unique_id = name
        self._image = image
        self._attr_available = available
        self._attr_is_on = is_on
        self.content_type = content_type
        self._stream_only = stream_only
        self.calls = 0

    @property
    def use_stream_for_stills(self) -> bool:
        return self._stream_only

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        self.calls += 1
        if isinstance(self._image, Exception):
            raise self._image
        return self._image


@pytest.fixture
async def cameras(hass: HomeAssistant) -> dict[str, FakeCamera]:
    """Register test cameras with the camera component."""
    assert await async_setup_component(hass, "camera", {})
    await hass.async_block_till_done()
    cams = {
        "front_door": FakeCamera("Front door", JPEG),
        "garage": FakeCamera("Garage", PNG, content_type="image/png"),
        "offline": FakeCamera("Offline", JPEG, available=False),
        "turned_off": FakeCamera("Turned off", JPEG, is_on=False),
        "no_image": FakeCamera("No image", None),
        "not_implemented": FakeCamera("Not implemented", NotImplementedError()),
        "broken": FakeCamera("Broken", HomeAssistantError("boom")),
        "garbage": FakeCamera("Garbage", b"<svg></svg>", content_type="image/svg+xml"),
        "stream_only": FakeCamera("Stream only", None, stream_only=True),
    }
    await hass.data[DATA_COMPONENT].async_add_entities(list(cams.values()))
    await hass.async_block_till_done()
    return cams


async def test_send_screen_shot(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
    cameras: dict[str, FakeCamera],
) -> None:
    """A fresh camera still is uploaded from memory."""
    result = await _call(
        hass,
        "send_screen_shot",
        {"who": "simon", "camera_entity": "camera.front_door", "message": "門口偵測到有人"},
        response=True,
    )
    assert result["status"] == "submitted"
    assert cameras["front_door"].calls == 1
    request = gateway_state.requests[0]
    assert request.image == JPEG
    assert request.image_content_type == "image/jpeg"
    assert request.image_filename == "front_door.jpg"
    assert request.fields["message"] == "門口偵測到有人"


async def test_send_screen_shot_png(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
    cameras: dict[str, FakeCamera],
) -> None:
    """PNG camera stills keep their real content type."""
    await _call(hass, "send_screen_shot", {"who": "simon", "camera_entity": "camera.garage"})
    request = gateway_state.requests[0]
    assert request.image_content_type == "image/png"
    assert "message" not in request.fields


@pytest.mark.parametrize(
    ("entity_id", "error", "key"),
    [
        ("camera.does_not_exist", ServiceValidationError, "camera_not_found"),
        ("camera.offline", HomeAssistantError, "camera_unavailable"),
        ("camera.turned_off", HomeAssistantError, "camera_image_failed"),
        ("camera.no_image", HomeAssistantError, "camera_image_failed"),
        ("camera.not_implemented", HomeAssistantError, "camera_image_failed"),
        ("camera.broken", HomeAssistantError, "camera_image_failed"),
        ("camera.stream_only", HomeAssistantError, "camera_image_failed"),
        ("camera.garbage", HomeAssistantError, "camera_image_invalid_format"),
    ],
)
async def test_send_screen_shot_camera_errors(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    gateway_state: MockGatewayState,
    cameras: dict[str, FakeCamera],
    entity_id: str,
    error: type[Exception],
    key: str,
) -> None:
    """Camera problems are explicit errors and nothing is sent."""
    with pytest.raises(error) as exc_info:
        await _call(hass, "send_screen_shot", {"who": "simon", "camera_entity": entity_id})
    assert exc_info.value.translation_key == key
    assert gateway_state.requests == []


@pytest.mark.parametrize("camera_entity", ["sensor.front_door", "front_door", ["camera.a"]])
async def test_send_screen_shot_requires_camera_entity(
    hass: HomeAssistant, setup_integration: MockConfigEntry, camera_entity: Any
) -> None:
    """camera_entity must be a single camera.* entity ID."""
    with pytest.raises(vol.Invalid):
        await _call(hass, "send_screen_shot", {"who": "simon", "camera_entity": camera_entity})


async def test_send_screen_shot_unknown_alias_skips_camera(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    cameras: dict[str, FakeCamera],
) -> None:
    """who is validated before the camera is triggered."""
    with pytest.raises(ServiceValidationError):
        await _call(
            hass, "send_screen_shot", {"who": "nobody", "camera_entity": "camera.front_door"}
        )
    assert cameras["front_door"].calls == 0
