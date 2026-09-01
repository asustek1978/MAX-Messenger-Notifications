from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api import MaxMessengerApi, MaxMessengerApiError
from .const import (
    CONF_EMERGENCY_CHAT_ID,
    CONF_TARGET_ID,
    CONF_TARGET_TYPE,
    DOMAIN,
    SERVICE_ANSWER_CALLBACK,
    SERVICE_BROADCAST,
    SERVICE_BROADCAST_IMAGE,
    SERVICE_BROADCAST_VIDEO,
    SERVICE_SEND_EMERGENCY,
    SERVICE_SEND_EMERGENCY_IMAGE,
    SERVICE_SEND_IMAGE,
    SERVICE_SEND_MESSAGE,
    SERVICE_SEND_VIDEO,
)
from .helpers import notification_recipients, settings, user_profiles, with_home_menu_button

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_TEXT = "message"
ATTR_FILE_PATH = "file_path"
ATTR_CHAT_ID = "chat_id"
ATTR_USER_ID = "user_id"
ATTR_USER_IDS = "user_ids"
ATTR_REQUIRED_PERMISSION = "required_permission"
ATTR_FORMAT = "format"
ATTR_NOTIFY = "notify"
ATTR_BUTTONS = "buttons"
ATTR_DISABLE_LINK_PREVIEW = "disable_link_preview"
ATTR_CALLBACK_ID = "callback_id"


def _validate_user_ids(value: Any) -> list[int]:
    if value in (None, "", {}, []):
        return []
    if isinstance(value, (list, tuple, set)):
        return [int(item) for item in value if str(item).strip()]
    if isinstance(value, dict):
        raise vol.Invalid("user_ids must be a list of MAX user IDs")
    try:
        return [int(value)]
    except (TypeError, ValueError) as err:
        raise vol.Invalid("user_ids must be a list of MAX user IDs") from err


COMMON_SEND = {
    vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    vol.Optional(ATTR_FORMAT, default="markdown"): vol.In(["markdown", "html", "plain"]),
    vol.Optional(ATTR_NOTIFY, default=True): cv.boolean,
    vol.Optional(ATTR_BUTTONS): list,
    vol.Optional(ATTR_DISABLE_LINK_PREVIEW, default=False): cv.boolean,
}

SEND_SCHEMA = vol.Schema(
    {
        **COMMON_SEND,
        vol.Required(ATTR_TEXT): cv.string,
        vol.Optional(ATTR_CHAT_ID): vol.Coerce(int),
        vol.Optional(ATTR_USER_ID): vol.Coerce(int),
    }
)

BROADCAST_SCHEMA = vol.Schema(
    {
        **COMMON_SEND,
        vol.Required(ATTR_TEXT): cv.string,
        vol.Optional(ATTR_USER_IDS): _validate_user_ids,
        vol.Optional(ATTR_REQUIRED_PERMISSION): cv.string,
    }
)

SEND_MEDIA_SCHEMA = vol.Schema(
    {
        **COMMON_SEND,
        vol.Required(ATTR_FILE_PATH): cv.string,
        vol.Optional(ATTR_TEXT, default=""): cv.string,
        vol.Optional(ATTR_CHAT_ID): vol.Coerce(int),
        vol.Optional(ATTR_USER_ID): vol.Coerce(int),
    }
)

BROADCAST_MEDIA_SCHEMA = vol.Schema(
    {
        **COMMON_SEND,
        vol.Required(ATTR_FILE_PATH): cv.string,
        vol.Optional(ATTR_TEXT, default=""): cv.string,
        vol.Optional(ATTR_USER_IDS): _validate_user_ids,
        vol.Optional(ATTR_REQUIRED_PERMISSION): cv.string,
    }
)

EMERGENCY_SCHEMA = vol.Schema(
    {
        **COMMON_SEND,
        vol.Required(ATTR_TEXT): cv.string,
    }
)

EMERGENCY_MEDIA_SCHEMA = vol.Schema(
    {
        **COMMON_SEND,
        vol.Required(ATTR_FILE_PATH): cv.string,
        vol.Optional(ATTR_TEXT, default=""): cv.string,
    }
)

ANSWER_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_CALLBACK_ID): cv.string,
        vol.Optional(ATTR_TEXT): cv.string,
        vol.Optional(ATTR_FORMAT, default="markdown"): vol.In(["markdown", "html", "plain"]),
        vol.Optional(ATTR_BUTTONS): list,
    }
)


def _fmt(call: ServiceCall) -> str | None:
    fmt = call.data.get(ATTR_FORMAT)
    return None if fmt == "plain" else fmt


def _entry_runtime(hass: HomeAssistant, entry_id: str | None) -> dict[str, Any]:
    entries = hass.data.get(DOMAIN, {}).get("entries", {})
    if entry_id:
        runtime = entries.get(entry_id)
        if runtime is None:
            raise HomeAssistantError(f"MAX Messenger Notifications config entry not found: {entry_id}")
        return runtime
    if len(entries) == 1:
        return next(iter(entries.values()))
    if not entries:
        raise HomeAssistantError("MAX Messenger Notifications is not configured")
    raise HomeAssistantError("Several MAX Messenger Notifications entries exist; specify config_entry_id")


def _resolve_single_target(call: ServiceCall, cfg: dict[str, Any]) -> tuple[str, int]:
    chat_id = call.data.get(ATTR_CHAT_ID)
    user_id = call.data.get(ATTR_USER_ID)
    if chat_id is not None and user_id is not None:
        raise HomeAssistantError("Specify only one of chat_id or user_id")
    if chat_id is not None:
        return "chat_id", int(chat_id)
    if user_id is not None:
        return "user_id", int(user_id)
    return str(cfg[CONF_TARGET_TYPE]), int(cfg[CONF_TARGET_ID])


def _emergency_chat_id(cfg: dict[str, Any]) -> int:
    value = cfg.get(CONF_EMERGENCY_CHAT_ID)
    if value is None or str(value).strip() == "":
        raise HomeAssistantError(
            "Emergency MAX channel is not configured. Open integration options and set emergency_chat_id."
        )
    try:
        return int(value)
    except (TypeError, ValueError) as err:
        raise HomeAssistantError("Invalid emergency_chat_id in MAX Messenger Notifications settings") from err


def _broadcast_targets(cfg: dict[str, Any], call: ServiceCall) -> list[tuple[str, int]]:
    profiles = user_profiles(cfg)
    required_permission = str(call.data.get(ATTR_REQUIRED_PERMISSION) or "").strip() or None
    recipients = notification_recipients(
        profiles,
        explicit_user_ids=call.data.get(ATTR_USER_IDS),
        required_permission=required_permission,
    )
    if not profiles:
        return [(str(cfg[CONF_TARGET_TYPE]), int(cfg[CONF_TARGET_ID]))]
    return [("user_id", user_id) for user_id in recipients]


async def _read_media(
    hass: HomeAssistant,
    file_path: str,
    media_type: str,
) -> tuple[bytes, str, str]:
    path = Path(file_path).expanduser()
    if not path.is_absolute():
        raise HomeAssistantError("file_path must be an absolute path")
    if not await hass.async_add_executor_job(path.is_file):
        raise HomeAssistantError(f"Media file not found: {file_path}")

    size = await hass.async_add_executor_job(lambda: path.stat().st_size)
    if size <= 0:
        raise HomeAssistantError(f"Media file is empty: {file_path}")
    if media_type == "video" and size > 250 * 1024 * 1024:
        raise HomeAssistantError("MAX supports video files up to 250 MB")

    if media_type == "video" and path.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm"}:
        raise HomeAssistantError("MAX supports MP4, MOV, MKV and WEBM video files")

    data = await hass.async_add_executor_job(path.read_bytes)
    fallback = "image/jpeg" if media_type == "image" else "video/mp4"
    content_type = mimetypes.guess_type(path.name)[0] or fallback
    if media_type == "image" and not content_type.startswith("image/"):
        raise HomeAssistantError(f"Unsupported image type: {content_type}")
    return data, path.name, content_type


async def register_services(hass: HomeAssistant) -> None:
    async def send_message(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        api: MaxMessengerApi = runtime["api"]
        cfg = settings(runtime["entry"])
        target_type, target_id = _resolve_single_target(call, cfg)
        try:
            await api.send_message(
                text=call.data[ATTR_TEXT],
                target_type=target_type,
                target_id=target_id,
                fmt=_fmt(call),
                notify=call.data[ATTR_NOTIFY],
                buttons=call.data.get(ATTR_BUTTONS),
                disable_link_preview=call.data[ATTR_DISABLE_LINK_PREVIEW],
            )
        except MaxMessengerApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def send_emergency(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        api: MaxMessengerApi = runtime["api"]
        target_id = _emergency_chat_id(settings(runtime["entry"]))
        try:
            await api.send_message(
                text=call.data[ATTR_TEXT],
                target_type="chat_id",
                target_id=target_id,
                fmt=_fmt(call),
                notify=call.data[ATTR_NOTIFY],
                buttons=call.data.get(ATTR_BUTTONS),
                disable_link_preview=call.data[ATTR_DISABLE_LINK_PREVIEW],
            )
        except MaxMessengerApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def broadcast(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        api: MaxMessengerApi = runtime["api"]
        targets = _broadcast_targets(settings(runtime["entry"]), call)
        try:
            for target_type, target_id in targets:
                await api.send_message(
                    text=call.data[ATTR_TEXT],
                    target_type=target_type,
                    target_id=target_id,
                    fmt=_fmt(call),
                    notify=call.data[ATTR_NOTIFY],
                    buttons=with_home_menu_button(call.data.get(ATTR_BUTTONS)),
                    disable_link_preview=call.data[ATTR_DISABLE_LINK_PREVIEW],
                )
        except MaxMessengerApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def send_image(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        api: MaxMessengerApi = runtime["api"]
        cfg = settings(runtime["entry"])
        target_type, target_id = _resolve_single_target(call, cfg)
        data, filename, content_type = await _read_media(hass, call.data[ATTR_FILE_PATH], "image")
        try:
            token = await api.upload_image(data=data, filename=filename, content_type=content_type)
            await api.send_image_token(
                token=token,
                text=call.data[ATTR_TEXT],
                target_type=target_type,
                target_id=target_id,
                fmt=_fmt(call),
                notify=call.data[ATTR_NOTIFY],
                buttons=call.data.get(ATTR_BUTTONS),
                disable_link_preview=call.data[ATTR_DISABLE_LINK_PREVIEW],
            )
        except MaxMessengerApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def send_emergency_image(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        api: MaxMessengerApi = runtime["api"]
        target_id = _emergency_chat_id(settings(runtime["entry"]))
        data, filename, content_type = await _read_media(hass, call.data[ATTR_FILE_PATH], "image")
        try:
            token = await api.upload_image(data=data, filename=filename, content_type=content_type)
            await api.send_image_token(
                token=token,
                text=call.data[ATTR_TEXT],
                target_type="chat_id",
                target_id=target_id,
                fmt=_fmt(call),
                notify=call.data[ATTR_NOTIFY],
                buttons=call.data.get(ATTR_BUTTONS),
                disable_link_preview=call.data[ATTR_DISABLE_LINK_PREVIEW],
            )
        except MaxMessengerApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def broadcast_image(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        api: MaxMessengerApi = runtime["api"]
        targets = _broadcast_targets(settings(runtime["entry"]), call)
        if not targets:
            return
        data, filename, content_type = await _read_media(hass, call.data[ATTR_FILE_PATH], "image")
        try:
            token = await api.upload_image(data=data, filename=filename, content_type=content_type)
            for target_type, target_id in targets:
                await api.send_image_token(
                    token=token,
                    text=call.data[ATTR_TEXT],
                    target_type=target_type,
                    target_id=target_id,
                    fmt=_fmt(call),
                    notify=call.data[ATTR_NOTIFY],
                    buttons=with_home_menu_button(call.data.get(ATTR_BUTTONS)),
                    disable_link_preview=call.data[ATTR_DISABLE_LINK_PREVIEW],
                )
        except MaxMessengerApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def send_video(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        api: MaxMessengerApi = runtime["api"]
        cfg = settings(runtime["entry"])
        target_type, target_id = _resolve_single_target(call, cfg)
        data, filename, content_type = await _read_media(hass, call.data[ATTR_FILE_PATH], "video")
        try:
            token = await api.upload_video(data=data, filename=filename, content_type=content_type)
            await api.send_video_token(
                token=token,
                text=call.data[ATTR_TEXT],
                target_type=target_type,
                target_id=target_id,
                fmt=_fmt(call),
                notify=call.data[ATTR_NOTIFY],
                buttons=call.data.get(ATTR_BUTTONS),
                disable_link_preview=call.data[ATTR_DISABLE_LINK_PREVIEW],
            )
        except MaxMessengerApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def broadcast_video(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        api: MaxMessengerApi = runtime["api"]
        targets = _broadcast_targets(settings(runtime["entry"]), call)
        if not targets:
            return
        data, filename, content_type = await _read_media(hass, call.data[ATTR_FILE_PATH], "video")
        try:
            token = await api.upload_video(data=data, filename=filename, content_type=content_type)
            for target_type, target_id in targets:
                await api.send_video_token(
                    token=token,
                    text=call.data[ATTR_TEXT],
                    target_type=target_type,
                    target_id=target_id,
                    fmt=_fmt(call),
                    notify=call.data[ATTR_NOTIFY],
                    buttons=with_home_menu_button(call.data.get(ATTR_BUTTONS)),
                    disable_link_preview=call.data[ATTR_DISABLE_LINK_PREVIEW],
                )
        except MaxMessengerApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def answer_callback(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        api: MaxMessengerApi = runtime["api"]
        try:
            await api.answer_callback(
                callback_id=call.data[ATTR_CALLBACK_ID],
                text=call.data.get(ATTR_TEXT),
                fmt=_fmt(call),
                buttons=call.data.get(ATTR_BUTTONS),
            )
        except MaxMessengerApiError as err:
            raise HomeAssistantError(str(err)) from err

    definitions = (
        (SERVICE_SEND_MESSAGE, send_message, SEND_SCHEMA),
        (SERVICE_SEND_EMERGENCY, send_emergency, EMERGENCY_SCHEMA),
        (SERVICE_BROADCAST, broadcast, BROADCAST_SCHEMA),
        (SERVICE_SEND_IMAGE, send_image, SEND_MEDIA_SCHEMA),
        (SERVICE_SEND_EMERGENCY_IMAGE, send_emergency_image, EMERGENCY_MEDIA_SCHEMA),
        (SERVICE_BROADCAST_IMAGE, broadcast_image, BROADCAST_MEDIA_SCHEMA),
        (SERVICE_SEND_VIDEO, send_video, SEND_MEDIA_SCHEMA),
        (SERVICE_BROADCAST_VIDEO, broadcast_video, BROADCAST_MEDIA_SCHEMA),
        (SERVICE_ANSWER_CALLBACK, answer_callback, ANSWER_SCHEMA),
    )

    for service_name, handler, schema in definitions:
        if not hass.services.has_service(DOMAIN, service_name):
            hass.services.async_register(DOMAIN, service_name, handler, schema=schema)
