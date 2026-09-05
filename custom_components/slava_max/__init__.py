from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service import async_set_service_schema

from .api import SlavaMaxApi, SlavaMaxApiError
from .const import (
    CONF_ALLOWED_USERS,
    CONF_EMERGENCY_CHAT_ID,
    CONF_FALLBACK_EMERGENCY,
    CONF_FALLBACK_HA_ENABLED,
    CONF_FALLBACK_HA_SERVICE,
    CONF_FALLBACK_REGULAR,
    CONF_FALLBACK_VK_ENABLED,
    CONF_FALLBACK_VK_ENTITY,
    CONF_POLLING,
    CONF_TARGET_ID,
    CONF_TARGET_TYPE,
    CONF_TOKEN,
    CONF_USER_ENABLED,
    CONF_USER_NAME,
    CONF_USER_PERMISSIONS,
    CONF_USERS,
    DOMAIN,
    EVENT_ACCESS_REQUEST,
    EVENT_NAME,
    PERM_ALL,
    PERM_NOTIFICATIONS,
    SERVICE_ANSWER_CALLBACK,
    SERVICE_BROADCAST,
    SERVICE_BROADCAST_OR_UPDATE,
    SERVICE_BROADCAST_IMAGE,
    SERVICE_BROADCAST_VIDEO,
    SERVICE_EDIT_MESSAGE,
    SERVICE_SEND_EMERGENCY,
    SERVICE_SEND_EMERGENCY_IMAGE,
    SERVICE_SEND_IMAGE,
    SERVICE_SEND_MESSAGE,
    SERVICE_SEND_OR_REPLACE,
    SERVICE_SEND_OR_UPDATE,
    SERVICE_SEND_VIDEO,
)

_LOGGER = logging.getLogger(__name__)

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_MESSAGE = "message"
ATTR_MESSAGE_ID = "message_id"
ATTR_KEY = "key"
ATTR_CHAT_ID = "chat_id"
ATTR_USER_ID = "user_id"
ATTR_USER_IDS = "user_ids"
ATTR_REQUIRED_PERMISSION = "required_permission"
ATTR_FORMAT = "format"
ATTR_NOTIFY = "notify"
ATTR_BUTTONS = "buttons"
ATTR_DISABLE_LINK_PREVIEW = "disable_link_preview"
ATTR_FILE_PATH = "file_path"
ATTR_CALLBACK_ID = "callback_id"
ATTR_EMERGENCY = "emergency"


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _format(value: Any) -> str | None:
    raw = str(value or "markdown").lower()
    return None if raw == "plain" else raw


def _buttons(value: Any) -> list[list[dict[str, Any]]] | None:
    if not value:
        return None
    if isinstance(value, list):
        return value
    raise HomeAssistantError("buttons должен быть списком строк кнопок")


def _with_home_menu_button(
    buttons: list[list[dict[str, Any]]] | None,
) -> list[list[dict[str, Any]]]:
    """Return buttons with the standard home-control button appended."""
    result: list[list[dict[str, Any]]] = []
    for row in buttons or []:
        if isinstance(row, list):
            result.append([dict(item) for item in row if isinstance(item, dict)])

    for row in result:
        for item in row:
            if str(item.get("payload", "")) == "home_main":
                return result

    result.append(
        [
            {
                "type": "callback",
                "text": "🏠 Управление домом",
                "payload": "home_main",
            }
        ]
    )
    return result


def _conf(entry: ConfigEntry) -> dict[str, Any]:
    return {**entry.data, **entry.options}


def _entry_by_id(hass: HomeAssistant, entry_id: str | None) -> ConfigEntry:
    entries = hass.config_entries.async_entries(DOMAIN)
    if entry_id:
        for entry in entries:
            if entry.entry_id == entry_id:
                return entry
        raise HomeAssistantError(f"Не найдена запись {DOMAIN}: {entry_id}")
    if not entries:
        raise HomeAssistantError("Интеграция MAX не настроена")
    if len(entries) > 1:
        raise HomeAssistantError("Укажите config_entry_id")
    return entries[0]


def _api(hass: HomeAssistant, entry: ConfigEntry) -> SlavaMaxApi:
    runtime = hass.data.setdefault(DOMAIN, {}).get(entry.entry_id)
    if runtime is None:
        raise HomeAssistantError("Интеграция MAX ещё не загружена")
    return runtime["api"]


def _resolve_single_target(call: ServiceCall, cfg: dict[str, Any]) -> tuple[str, int]:
    chat_id = call.data.get(ATTR_CHAT_ID)
    user_id = call.data.get(ATTR_USER_ID)
    if chat_id is not None and user_id is not None:
        raise HomeAssistantError("Specify only one of chat_id or user_id")
    if chat_id is not None:
        return ("chat_id", int(chat_id))
    if user_id is not None:
        return ("user_id", int(user_id))
    return (str(cfg[CONF_TARGET_TYPE]), int(cfg[CONF_TARGET_ID]))


def _emergency_chat_id(cfg: dict[str, Any]) -> int:
    raw = cfg.get(CONF_EMERGENCY_CHAT_ID)
    if raw in (None, ""):
        raise HomeAssistantError("Аварийный chat_id не настроен")
    return int(raw)


def _configured_users(cfg: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}

    for row in cfg.get(CONF_USERS, []) or []:
        if not isinstance(row, dict):
            continue
        try:
            user_id = int(row.get("user_id"))
        except (TypeError, ValueError):
            continue
        result[user_id] = {
            CONF_USER_NAME: str(row.get(CONF_USER_NAME, "") or ""),
            CONF_USER_ENABLED: _to_bool(row.get(CONF_USER_ENABLED), True),
            CONF_USER_PERMISSIONS: list(row.get(CONF_USER_PERMISSIONS, []) or []),
        }

    for raw in cfg.get(CONF_ALLOWED_USERS, []) or []:
        try:
            user_id = int(raw)
        except (TypeError, ValueError):
            continue
        result.setdefault(
            user_id,
            {
                CONF_USER_NAME: "",
                CONF_USER_ENABLED: True,
                CONF_USER_PERMISSIONS: [PERM_ALL],
            },
        )

    if not result and str(cfg.get(CONF_TARGET_TYPE)) == "user_id":
        try:
            user_id = int(cfg.get(CONF_TARGET_ID))
        except (TypeError, ValueError):
            pass
        else:
            result[user_id] = {
                CONF_USER_NAME: "",
                CONF_USER_ENABLED: True,
                CONF_USER_PERMISSIONS: [PERM_ALL],
            }

    return result


def _permissions(cfg: dict[str, Any], user_id: int) -> set[str]:
    user = _configured_users(cfg).get(int(user_id))
    if not user or not _to_bool(user.get(CONF_USER_ENABLED), True):
        return set()
    return {str(x) for x in user.get(CONF_USER_PERMISSIONS, []) or []}


def _is_allowed(cfg: dict[str, Any], user_id: int, permission: str | None = None) -> bool:
    perms = _permissions(cfg, int(user_id))
    if not perms:
        return False
    if PERM_ALL in perms:
        return True
    if permission is None:
        return True
    return permission in perms


def _configured_recipient_ids(
    cfg: dict[str, Any],
    required_permission: str | None = None,
) -> list[int]:
    return [
        user_id
        for user_id in sorted(_configured_users(cfg))
        if _is_allowed(cfg, user_id, required_permission)
    ]


def _parse_user_ids(raw: Any) -> list[int]:
    if raw in (None, "", []):
        return []
    if isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        values = [part.strip() for part in str(raw).replace(";", ",").split(",")]
    result: list[int] = []
    for value in values:
        if value in (None, ""):
            continue
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            raise HomeAssistantError(f"Некорректный MAX user_id: {value}")
    return result


def _fallback_flags(cfg: dict[str, Any], emergency: bool) -> tuple[bool, bool]:
    mode = (
        str(cfg.get(CONF_FALLBACK_EMERGENCY, "ha_vk") or "ha_vk")
        if emergency
        else str(cfg.get(CONF_FALLBACK_REGULAR, "ha") or "ha")
    )
    mode = mode.strip().lower()
    if mode in {"", "none", "off", "false"}:
        return (False, False)
    if mode == "ha":
        return (True, False)
    if mode == "vk":
        return (False, True)
    if mode in {"ha_vk", "vk_ha", "both", "ha+vk", "vk+ha"}:
        return (True, True)
    return ("ha" in mode, "vk" in mode)


async def _call_service(
    hass: HomeAssistant,
    domain: str,
    service: str,
    data: dict[str, Any],
) -> None:
    await hass.services.async_call(domain, service, data, blocking=False)


async def _fallback_notify(
    hass: HomeAssistant,
    cfg: dict[str, Any],
    message: str,
    *,
    emergency: bool,
    error: Exception,
) -> None:
    use_ha, use_vk = _fallback_flags(cfg, emergency)
    prefix = "🚨 MAX недоступен" if emergency else "⚠️ MAX недоступен"
    fallback_message = f"{prefix}\n{message}\n\nОшибка: {error}"

    if use_ha and _to_bool(cfg.get(CONF_FALLBACK_HA_ENABLED), True):
        service_ref = str(cfg.get(CONF_FALLBACK_HA_SERVICE, "") or "").strip()
        if service_ref:
            if "." in service_ref:
                domain, service = service_ref.split(".", 1)
            else:
                domain, service = "notify", service_ref
            try:
                await _call_service(
                    hass,
                    domain,
                    service,
                    {
                        "title": "Умный дом — резервное уведомление",
                        "message": fallback_message,
                    },
                )
            except Exception:
                _LOGGER.exception("Ошибка резервной отправки через Home Assistant")

    if use_vk and _to_bool(cfg.get(CONF_FALLBACK_VK_ENABLED), False):
        entity_id = str(cfg.get(CONF_FALLBACK_VK_ENTITY, "") or "").strip()
        if entity_id:
            try:
                await _call_service(
                    hass,
                    "text",
                    "set_value",
                    {"entity_id": entity_id, "value": fallback_message},
                )
            except Exception:
                _LOGGER.exception("Ошибка резервной отправки через VK")


async def _send_message(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    message: str,
    target_type: str,
    target_id: int,
    fmt: str | None,
    notify: bool,
    buttons: list[list[dict[str, Any]]] | None,
    disable_link_preview: bool,
    emergency: bool,
    do_fallback: bool = True,
) -> dict[str, Any] | None:
    try:
        return await _api(hass, entry).send_message(
            text=message,
            target_type=target_type,
            target_id=target_id,
            fmt=fmt,
            notify=notify,
            buttons=buttons,
            disable_link_preview=disable_link_preview,
        )
    except Exception as err:
        if do_fallback:
            await _fallback_notify(
                hass,
                _conf(entry),
                message,
                emergency=emergency,
                error=err,
            )
        raise


async def _send_image(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    file_path: str,
    message: str,
    target_type: str,
    target_id: int,
    fmt: str | None,
    notify: bool,
    buttons: list[list[dict[str, Any]]] | None,
    disable_link_preview: bool,
    emergency: bool,
    do_fallback: bool = True,
) -> dict[str, Any] | None:
    api = _api(hass, entry)

    try:
        path = hass.config.path(file_path) if not os.path.isabs(file_path) else file_path
        data = await hass.async_add_executor_job(_read_bytes, path)
        content_type = mimetypes.guess_type(path)[0] or "image/jpeg"
        token = await api.upload_image(
            data=data,
            filename=os.path.basename(path) or "image.jpg",
            content_type=content_type,
        )
        return await api.send_image_token(
            token=token,
            text=message,
            target_type=target_type,
            target_id=target_id,
            fmt=fmt,
            notify=notify,
            buttons=buttons,
            disable_link_preview=disable_link_preview,
        )
    except Exception as err:
        if do_fallback:
            await _fallback_notify(
                hass,
                _conf(entry),
                message or f"Не удалось отправить изображение {file_path}",
                emergency=emergency,
                error=err,
            )
        raise


async def _send_video(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    file_path: str,
    message: str,
    target_type: str,
    target_id: int,
    fmt: str | None,
    notify: bool,
    buttons: list[list[dict[str, Any]]] | None,
    disable_link_preview: bool,
    emergency: bool,
    do_fallback: bool = True,
) -> dict[str, Any] | None:
    api = _api(hass, entry)

    try:
        path = hass.config.path(file_path) if not os.path.isabs(file_path) else file_path
        data = await hass.async_add_executor_job(_read_bytes, path)
        content_type = mimetypes.guess_type(path)[0] or "video/mp4"
        token = await api.upload_video(
            data=data,
            filename=os.path.basename(path) or "video.mp4",
            content_type=content_type,
        )
        return await api.send_video_token(
            token=token,
            text=message,
            target_type=target_type,
            target_id=target_id,
            fmt=fmt,
            notify=notify,
            buttons=buttons,
            disable_link_preview=disable_link_preview,
        )
    except Exception as err:
        if do_fallback:
            await _fallback_notify(
                hass,
                _conf(entry),
                message or f"Не удалось отправить видео {file_path}",
                emergency=emergency,
                error=err,
            )
        raise


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _extract_message_id(result: dict[str, Any] | None) -> str | None:
    if not isinstance(result, dict):
        return None
    candidates = [
        result.get("message_id"),
        result.get("mid"),
    ]
    body = result.get("body")
    if isinstance(body, dict):
        candidates.extend([body.get("mid"), body.get("message_id")])
    message = result.get("message")
    if isinstance(message, dict):
        candidates.extend([message.get("mid"), message.get("message_id")])
    for value in candidates:
        if value not in (None, ""):
            return str(value)
    return None


def _message_store(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    return hass.data.setdefault(DOMAIN, {}).setdefault("message_store", {})


def _message_store_key(entry: ConfigEntry, key: str, target_type: str, target_id: int) -> str:
    return f"{entry.entry_id}:{target_type}:{int(target_id)}:{key}"


async def _send_or_update(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    key: str,
    message: str,
    target_type: str,
    target_id: int,
    fmt: str | None,
    notify: bool,
    buttons: list[list[dict[str, Any]]] | None,
    disable_link_preview: bool,
    emergency: bool,
) -> dict[str, Any] | None:
    store = _message_store(hass)
    store_key = _message_store_key(entry, key, target_type, target_id)
    previous = store.get(store_key)
    api = _api(hass, entry)

    if previous and previous.get("message_id"):
        try:
            result = await api.edit_message(
                message_id=str(previous["message_id"]),
                text=message,
                fmt=fmt,
                notify=notify,
                buttons=buttons,
            )
            previous["message"] = message
            previous["result"] = result
            return result
        except Exception as err:
            _LOGGER.warning(
                "Не удалось обновить MAX сообщение key=%s target=%s:%s: %s. Будет создано новое.",
                key,
                target_type,
                target_id,
                err,
            )

    result = await _send_message(
        hass,
        entry,
        message=message,
        target_type=target_type,
        target_id=target_id,
        fmt=fmt,
        notify=notify,
        buttons=buttons,
        disable_link_preview=disable_link_preview,
        emergency=emergency,
    )
    message_id = _extract_message_id(result)
    if message_id:
        store[store_key] = {
            "message_id": message_id,
            "message": message,
            "target_type": target_type,
            "target_id": int(target_id),
        }
    return result


async def _send_or_replace(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    key: str,
    message: str,
    target_type: str,
    target_id: int,
    fmt: str | None,
    notify: bool,
    buttons: list[list[dict[str, Any]]] | None,
    disable_link_preview: bool,
    emergency: bool,
) -> dict[str, Any] | None:
    """Send a fresh message, then remove the previous message for this key."""
    store = _message_store(hass)
    store_key = _message_store_key(entry, key, target_type, target_id)
    previous = store.get(store_key)
    api = _api(hass, entry)

    result = await _send_message(
        hass,
        entry,
        message=message,
        target_type=target_type,
        target_id=target_id,
        fmt=fmt,
        notify=notify,
        buttons=buttons,
        disable_link_preview=disable_link_preview,
        emergency=emergency,
    )
    new_message_id = _extract_message_id(result)

    if new_message_id:
        store[store_key] = {
            "message_id": new_message_id,
            "message": message,
            "target_type": target_type,
            "target_id": int(target_id),
        }

    old_message_id = str((previous or {}).get("message_id") or "").strip()
    if old_message_id and new_message_id and old_message_id != new_message_id:
        try:
            await api.delete_message(message_id=old_message_id)
        except Exception as err:
            _LOGGER.warning(
                "Новое MAX сообщение отправлено, но старое не удалено key=%s target=%s:%s old_mid=%s: %s",
                key,
                target_type,
                target_id,
                old_message_id,
                err,
            )

    return result


async def _broadcast_message(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    message: str,
    user_ids: list[int],
    required_permission: str | None,
    fmt: str | None,
    notify: bool,
    buttons: list[list[dict[str, Any]]] | None,
    disable_link_preview: bool,
) -> None:
    cfg = _conf(entry)
    targets = user_ids or _configured_recipient_ids(cfg, required_permission)
    if not targets:
        raise HomeAssistantError("Нет разрешённых получателей MAX")

    failures: list[str] = []
    sent = 0
    for user_id in targets:
        if not _is_allowed(cfg, user_id, required_permission):
            _LOGGER.warning(
                "Пропуск рассылки MAX user_id=%s permission=%s: нет права",
                user_id,
                required_permission,
            )
            continue
        try:
            await _send_message(
                hass,
                entry,
                message=message,
                target_type="user_id",
                target_id=int(user_id),
                fmt=fmt,
                notify=notify,
                buttons=buttons,
                disable_link_preview=disable_link_preview,
                emergency=False,
                do_fallback=False,
            )
            sent += 1
        except Exception as err:
            failures.append(f"{user_id}: {err}")

    if failures:
        err = SlavaMaxApiError("; ".join(failures))
        await _fallback_notify(hass, cfg, message, emergency=False, error=err)
        if sent == 0:
            raise HomeAssistantError(f"MAX broadcast failed: {err}")


async def _broadcast_or_update_message(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    key: str,
    message: str,
    user_ids: list[int],
    required_permission: str | None,
    fmt: str | None,
    notify: bool,
    buttons: list[list[dict[str, Any]]] | None,
    disable_link_preview: bool,
) -> None:
    cfg = _conf(entry)
    targets = user_ids or _configured_recipient_ids(cfg, required_permission)
    if not targets:
        raise HomeAssistantError("Нет разрешённых получателей MAX")

    failures: list[str] = []
    sent = 0
    for user_id in targets:
        if not _is_allowed(cfg, user_id, required_permission):
            _LOGGER.warning(
                "Пропуск MAX broadcast_or_update user_id=%s permission=%s: нет права",
                user_id,
                required_permission,
            )
            continue
        try:
            await _send_or_update(
                hass,
                entry,
                key=key,
                message=message,
                target_type="user_id",
                target_id=int(user_id),
                fmt=fmt,
                notify=notify,
                buttons=buttons,
                disable_link_preview=disable_link_preview,
                emergency=False,
            )
            sent += 1
        except Exception as err:
            failures.append(f"{user_id}: {err}")

    if failures:
        err = SlavaMaxApiError("; ".join(failures))
        await _fallback_notify(hass, cfg, message, emergency=False, error=err)
        if sent == 0:
            raise HomeAssistantError(f"MAX broadcast_or_update failed: {err}")


async def _broadcast_image(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    file_path: str,
    message: str,
    user_ids: list[int],
    required_permission: str | None,
    fmt: str | None,
    notify: bool,
    buttons: list[list[dict[str, Any]]] | None,
    disable_link_preview: bool,
) -> None:
    cfg = _conf(entry)
    targets = user_ids or _configured_recipient_ids(cfg, required_permission)
    if not targets:
        raise HomeAssistantError("Нет разрешённых получателей MAX")

    path = hass.config.path(file_path) if not os.path.isabs(file_path) else file_path
    data = await hass.async_add_executor_job(_read_bytes, path)
    content_type = mimetypes.guess_type(path)[0] or "image/jpeg"
    token = await _api(hass, entry).upload_image(
        data=data,
        filename=os.path.basename(path) or "image.jpg",
        content_type=content_type,
    )

    failures: list[str] = []
    sent = 0
    for user_id in targets:
        if not _is_allowed(cfg, user_id, required_permission):
            continue
        try:
            await _api(hass, entry).send_image_token(
                token=token,
                text=message,
                target_type="user_id",
                target_id=int(user_id),
                fmt=fmt,
                notify=notify,
                buttons=buttons,
                disable_link_preview=disable_link_preview,
            )
            sent += 1
        except Exception as err:
            failures.append(f"{user_id}: {err}")

    if failures:
        err = SlavaMaxApiError("; ".join(failures))
        await _fallback_notify(
            hass,
            cfg,
            message or f"Не удалось разослать изображение {file_path}",
            emergency=False,
            error=err,
        )
        if sent == 0:
            raise HomeAssistantError(f"MAX image broadcast failed: {err}")


async def _broadcast_video(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    file_path: str,
    message: str,
    user_ids: list[int],
    required_permission: str | None,
    fmt: str | None,
    notify: bool,
    buttons: list[list[dict[str, Any]]] | None,
    disable_link_preview: bool,
) -> None:
    cfg = _conf(entry)
    targets = user_ids or _configured_recipient_ids(cfg, required_permission)
    if not targets:
        raise HomeAssistantError("Нет разрешённых получателей MAX")

    path = hass.config.path(file_path) if not os.path.isabs(file_path) else file_path
    data = await hass.async_add_executor_job(_read_bytes, path)
    content_type = mimetypes.guess_type(path)[0] or "video/mp4"
    token = await _api(hass, entry).upload_video(
        data=data,
        filename=os.path.basename(path) or "video.mp4",
        content_type=content_type,
    )

    failures: list[str] = []
    sent = 0
    for user_id in targets:
        if not _is_allowed(cfg, user_id, required_permission):
            continue
        try:
            await _api(hass, entry).send_video_token(
                token=token,
                text=message,
                target_type="user_id",
                target_id=int(user_id),
                fmt=fmt,
                notify=notify,
                buttons=buttons,
                disable_link_preview=disable_link_preview,
            )
            sent += 1
        except Exception as err:
            failures.append(f"{user_id}: {err}")

    if failures:
        err = SlavaMaxApiError("; ".join(failures))
        await _fallback_notify(
            hass,
            cfg,
            message or f"Не удалось разослать видео {file_path}",
            emergency=False,
            error=err,
        )
        if sent == 0:
            raise HomeAssistantError(f"MAX video broadcast failed: {err}")


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault("message_store", {})
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    cfg = _conf(entry)
    api = SlavaMaxApi(async_get_clientsession(hass), cfg[CONF_TOKEN])

    runtime: dict[str, Any] = {
        "api": api,
        "marker": None,
        "task": None,
        "stop": None,
    }
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    if _to_bool(cfg.get(CONF_POLLING), True):
        stop_event = asyncio.Event()
        runtime["stop"] = stop_event
        runtime["task"] = hass.async_create_task(
            _poll_loop(hass, entry, api, runtime, stop_event),
            f"{DOMAIN}_{entry.entry_id}_poll",
        )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime = hass.data.setdefault(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime:
        stop_event: asyncio.Event | None = runtime.get("stop")
        task: asyncio.Task | None = runtime.get("task")
        if stop_event:
            stop_event.set()
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def _poll_loop(
    hass: HomeAssistant,
    entry: ConfigEntry,
    api: SlavaMaxApi,
    runtime: dict[str, Any],
    stop_event: asyncio.Event,
) -> None:
    delay = 2
    while not stop_event.is_set():
        try:
            data = await api.get_updates(runtime.get("marker"), timeout=30)
            marker = data.get("marker")
            if marker is not None:
                try:
                    runtime["marker"] = int(marker)
                except (TypeError, ValueError):
                    pass
            updates = data.get("updates") or []
            if isinstance(updates, list):
                for update in updates:
                    if isinstance(update, dict):
                        _handle_update(hass, entry, update)
            delay = 2
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.warning("Ошибка MAX polling: %s", err)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, 60)


def _extract_user(update: dict[str, Any]) -> tuple[int | None, str | None]:
    candidates: list[dict[str, Any]] = []

    user_locale = update.get("user_locale")
    if isinstance(user_locale, dict):
        candidates.append(user_locale)

    message = update.get("message")
    if isinstance(message, dict):
        sender = message.get("sender")
        if isinstance(sender, dict):
            candidates.append(sender)
        recipient = message.get("recipient")
        if isinstance(recipient, dict):
            candidates.append(recipient)

    callback = update.get("callback")
    if isinstance(callback, dict):
        callback_user = callback.get("user")
        if isinstance(callback_user, dict):
            candidates.append(callback_user)

    for item in candidates:
        raw_id = item.get("user_id")
        if raw_id is None:
            continue
        try:
            user_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        name = (
            item.get("name")
            or item.get("first_name")
            or item.get("username")
        )
        return user_id, str(name) if name else None

    return None, None


def _event_type(update: dict[str, Any]) -> str:
    raw = str(update.get("update_type") or update.get("type") or "update")
    mapping = {
        "message_created": "message",
        "message_callback": "callback",
        "bot_started": "bot_started",
    }
    return mapping.get(raw, raw)


def _callback_payload(update: dict[str, Any]) -> tuple[str | None, str | None]:
    callback = update.get("callback")
    if not isinstance(callback, dict):
        return None, None
    payload = callback.get("payload")
    callback_id = callback.get("callback_id") or callback.get("id")
    return (
        str(payload) if payload is not None else None,
        str(callback_id) if callback_id is not None else None,
    )


def _message_text(update: dict[str, Any]) -> str | None:
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    body = message.get("body")
    if isinstance(body, dict):
        text = body.get("text")
        if text is not None:
            return str(text)
    text = message.get("text")
    return str(text) if text is not None else None


def _handle_update(hass: HomeAssistant, entry: ConfigEntry, update: dict[str, Any]) -> None:
    cfg = _conf(entry)
    user_id, user_name = _extract_user(update)
    event_type = _event_type(update)
    payload, callback_id = _callback_payload(update)
    text = _message_text(update)

    if user_id is not None and not _is_allowed(cfg, user_id):
        hass.bus.async_fire(
            EVENT_ACCESS_REQUEST,
            {
                "config_entry_id": entry.entry_id,
                "user_id": user_id,
                "user_name": user_name,
                "event_type": event_type,
                "text": text,
                "payload": payload,
            },
        )
        return

    hass.bus.async_fire(
        EVENT_NAME,
        {
            "config_entry_id": entry.entry_id,
            "event_type": event_type,
            "user_id": user_id,
            "user_name": user_name,
            "text": text,
            "payload": payload,
            "callback_id": callback_id,
            "raw": update,
        },
    )


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SEND_MESSAGE):
        return

    entry_selector = vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string

    async def handle_send_message(call: ServiceCall) -> None:
        entry = _entry_by_id(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        cfg = _conf(entry)
        target_type, target_id = _resolve_single_target(call, cfg)
        buttons = _buttons(call.data.get(ATTR_BUTTONS))
        if target_type == "user_id":
            buttons = _with_home_menu_button(buttons)
        try:
            await _send_message(
                hass,
                entry,
                message=str(call.data[ATTR_MESSAGE]),
                target_type=target_type,
                target_id=target_id,
                fmt=_format(call.data.get(ATTR_FORMAT)),
                notify=_to_bool(call.data.get(ATTR_NOTIFY), True),
                buttons=buttons,
                disable_link_preview=_to_bool(
                    call.data.get(ATTR_DISABLE_LINK_PREVIEW), False
                ),
                emergency=False,
            )
        except Exception as err:
            raise HomeAssistantError(f"Не удалось отправить сообщение в MAX: {err}") from err

    async def handle_edit_message(call: ServiceCall) -> None:
        entry = _entry_by_id(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        emergency = _to_bool(call.data.get(ATTR_EMERGENCY), False)
        try:
            await _api(hass, entry).edit_message(
                message_id=str(call.data[ATTR_MESSAGE_ID]),
                text=str(call.data[ATTR_MESSAGE]),
                fmt=_format(call.data.get(ATTR_FORMAT)),
                notify=_to_bool(call.data.get(ATTR_NOTIFY), True),
                buttons=_buttons(call.data.get(ATTR_BUTTONS)),
            )
        except Exception as err:
            await _fallback_notify(
                hass,
                _conf(entry),
                str(call.data[ATTR_MESSAGE]),
                emergency=emergency,
                error=err,
            )
            raise HomeAssistantError(f"Не удалось изменить сообщение MAX: {err}") from err

    async def handle_send_or_update(call: ServiceCall) -> None:
        entry = _entry_by_id(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        cfg = _conf(entry)
        emergency = _to_bool(call.data.get(ATTR_EMERGENCY), False)
        if emergency:
            explicit_chat_id = call.data.get(ATTR_CHAT_ID)
            explicit_user_id = call.data.get(ATTR_USER_ID)
            if explicit_user_id is not None:
                raise HomeAssistantError(
                    "For emergency send_or_update use chat_id, not user_id"
                )
            target_type = "chat_id"
            target_id = (
                int(explicit_chat_id)
                if explicit_chat_id is not None
                else _emergency_chat_id(cfg)
            )
        else:
            target_type, target_id = _resolve_single_target(call, cfg)

        buttons = _buttons(call.data.get(ATTR_BUTTONS))
        if not emergency and target_type == "user_id":
            buttons = _with_home_menu_button(buttons)
        try:
            await _send_or_update(
                hass,
                entry,
                key=str(call.data[ATTR_KEY]).strip(),
                message=str(call.data[ATTR_MESSAGE]),
                target_type=target_type,
                target_id=target_id,
                fmt=_format(call.data.get(ATTR_FORMAT)),
                notify=_to_bool(call.data.get(ATTR_NOTIFY), True),
                buttons=buttons,
                disable_link_preview=_to_bool(
                    call.data.get(ATTR_DISABLE_LINK_PREVIEW), False
                ),
                emergency=emergency,
            )
        except Exception as err:
            raise HomeAssistantError(f"Не удалось отправить/обновить MAX: {err}") from err

    async def handle_send_or_replace(call: ServiceCall) -> None:
        entry = _entry_by_id(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        cfg = _conf(entry)
        emergency = _to_bool(call.data.get(ATTR_EMERGENCY), False)
        if emergency:
            explicit_chat_id = call.data.get(ATTR_CHAT_ID)
            explicit_user_id = call.data.get(ATTR_USER_ID)
            if explicit_user_id is not None:
                raise HomeAssistantError(
                    "For emergency send_or_replace use chat_id, not user_id"
                )
            target_type = "chat_id"
            target_id = (
                int(explicit_chat_id)
                if explicit_chat_id is not None
                else _emergency_chat_id(cfg)
            )
        else:
            target_type, target_id = _resolve_single_target(call, cfg)

        buttons = _buttons(call.data.get(ATTR_BUTTONS))
        if not emergency and target_type == "user_id":
            buttons = _with_home_menu_button(buttons)
        try:
            await _send_or_replace(
                hass,
                entry,
                key=str(call.data[ATTR_KEY]).strip(),
                message=str(call.data[ATTR_MESSAGE]),
                target_type=target_type,
                target_id=target_id,
                fmt=_format(call.data.get(ATTR_FORMAT)),
                notify=_to_bool(call.data.get(ATTR_NOTIFY), True),
                buttons=buttons,
                disable_link_preview=_to_bool(
                    call.data.get(ATTR_DISABLE_LINK_PREVIEW), False
                ),
                emergency=emergency,
            )
        except Exception as err:
            raise HomeAssistantError(f"Не удалось заменить MAX сообщение: {err}") from err

    async def handle_send_emergency(call: ServiceCall) -> None:
        entry = _entry_by_id(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        cfg = _conf(entry)
        try:
            await _send_message(
                hass,
                entry,
                message=str(call.data[ATTR_MESSAGE]),
                target_type="chat_id",
                target_id=_emergency_chat_id(cfg),
                fmt=_format(call.data.get(ATTR_FORMAT)),
                notify=_to_bool(call.data.get(ATTR_NOTIFY), True),
                buttons=_buttons(call.data.get(ATTR_BUTTONS)),
                disable_link_preview=_to_bool(
                    call.data.get(ATTR_DISABLE_LINK_PREVIEW), False
                ),
                emergency=True,
            )
        except Exception as err:
            raise HomeAssistantError(f"Не удалось отправить аварийное сообщение: {err}") from err

    async def handle_broadcast(call: ServiceCall) -> None:
        entry = _entry_by_id(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        buttons = _with_home_menu_button(_buttons(call.data.get(ATTR_BUTTONS)))
        try:
            await _broadcast_message(
                hass,
                entry,
                message=str(call.data[ATTR_MESSAGE]),
                user_ids=_parse_user_ids(call.data.get(ATTR_USER_IDS)),
                required_permission=(
                    str(call.data.get(ATTR_REQUIRED_PERMISSION)).strip()
                    if call.data.get(ATTR_REQUIRED_PERMISSION)
                    else None
                ),
                fmt=_format(call.data.get(ATTR_FORMAT)),
                notify=_to_bool(call.data.get(ATTR_NOTIFY), True),
                buttons=buttons,
                disable_link_preview=_to_bool(
                    call.data.get(ATTR_DISABLE_LINK_PREVIEW), False
                ),
            )
        except Exception as err:
            raise HomeAssistantError(f"Не удалось выполнить рассылку MAX: {err}") from err

    async def handle_broadcast_or_update(call: ServiceCall) -> None:
        entry = _entry_by_id(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        buttons = _with_home_menu_button(_buttons(call.data.get(ATTR_BUTTONS)))
        try:
            await _broadcast_or_update_message(
                hass,
                entry,
                key=str(call.data[ATTR_KEY]).strip(),
                message=str(call.data[ATTR_MESSAGE]),
                user_ids=_parse_user_ids(call.data.get(ATTR_USER_IDS)),
                required_permission=(
                    str(call.data.get(ATTR_REQUIRED_PERMISSION)).strip()
                    if call.data.get(ATTR_REQUIRED_PERMISSION)
                    else None
                ),
                fmt=_format(call.data.get(ATTR_FORMAT)),
                notify=_to_bool(call.data.get(ATTR_NOTIFY), True),
                buttons=buttons,
                disable_link_preview=_to_bool(
                    call.data.get(ATTR_DISABLE_LINK_PREVIEW), False
                ),
            )
        except Exception as err:
            raise HomeAssistantError(
                f"Не удалось выполнить broadcast_or_update MAX: {err}"
            ) from err

    async def handle_send_image(call: ServiceCall) -> None:
        entry = _entry_by_id(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        cfg = _conf(entry)
        target_type, target_id = _resolve_single_target(call, cfg)
        buttons = _buttons(call.data.get(ATTR_BUTTONS))
        if target_type == "user_id":
            buttons = _with_home_menu_button(buttons)
        try:
            await _send_image(
                hass,
                entry,
                file_path=str(call.data[ATTR_FILE_PATH]),
                message=str(call.data.get(ATTR_MESSAGE) or ""),
                target_type=target_type,
                target_id=target_id,
                fmt=_format(call.data.get(ATTR_FORMAT)),
                notify=_to_bool(call.data.get(ATTR_NOTIFY), True),
                buttons=buttons,
                disable_link_preview=_to_bool(
                    call.data.get(ATTR_DISABLE_LINK_PREVIEW), False
                ),
                emergency=False,
            )
        except Exception as err:
            raise HomeAssistantError(f"Не удалось отправить изображение в MAX: {err}") from err

    async def handle_send_emergency_image(call: ServiceCall) -> None:
        entry = _entry_by_id(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        cfg = _conf(entry)
        try:
            await _send_image(
                hass,
                entry,
                file_path=str(call.data[ATTR_FILE_PATH]),
                message=str(call.data.get(ATTR_MESSAGE) or ""),
                target_type="chat_id",
                target_id=_emergency_chat_id(cfg),
                fmt=_format(call.data.get(ATTR_FORMAT)),
                notify=_to_bool(call.data.get(ATTR_NOTIFY), True),
                buttons=_buttons(call.data.get(ATTR_BUTTONS)),
                disable_link_preview=_to_bool(
                    call.data.get(ATTR_DISABLE_LINK_PREVIEW), False
                ),
                emergency=True,
            )
        except Exception as err:
            raise HomeAssistantError(
                f"Не удалось отправить аварийное изображение в MAX: {err}"
            ) from err

    async def handle_broadcast_image(call: ServiceCall) -> None:
        entry = _entry_by_id(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        buttons = _with_home_menu_button(_buttons(call.data.get(ATTR_BUTTONS)))
        try:
            await _broadcast_image(
                hass,
                entry,
                file_path=str(call.data[ATTR_FILE_PATH]),
                message=str(call.data.get(ATTR_MESSAGE) or ""),
                user_ids=_parse_user_ids(call.data.get(ATTR_USER_IDS)),
                required_permission=(
                    str(call.data.get(ATTR_REQUIRED_PERMISSION) or "cameras").strip()
                    or "cameras"
                ),
                fmt=_format(call.data.get(ATTR_FORMAT)),
                notify=_to_bool(call.data.get(ATTR_NOTIFY), True),
                buttons=buttons,
                disable_link_preview=_to_bool(
                    call.data.get(ATTR_DISABLE_LINK_PREVIEW), False
                ),
            )
        except Exception as err:
            raise HomeAssistantError(
                f"Не удалось выполнить рассылку изображения MAX: {err}"
            ) from err

    async def handle_send_video(call: ServiceCall) -> None:
        entry = _entry_by_id(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        cfg = _conf(entry)
        target_type, target_id = _resolve_single_target(call, cfg)
        buttons = _buttons(call.data.get(ATTR_BUTTONS))
        if target_type == "user_id":
            buttons = _with_home_menu_button(buttons)
        try:
            await _send_video(
                hass,
                entry,
                file_path=str(call.data[ATTR_FILE_PATH]),
                message=str(call.data.get(ATTR_MESSAGE) or ""),
                target_type=target_type,
                target_id=target_id,
                fmt=_format(call.data.get(ATTR_FORMAT)),
                notify=_to_bool(call.data.get(ATTR_NOTIFY), True),
                buttons=buttons,
                disable_link_preview=_to_bool(
                    call.data.get(ATTR_DISABLE_LINK_PREVIEW), False
                ),
                emergency=False,
            )
        except Exception as err:
            raise HomeAssistantError(f"Не удалось отправить видео в MAX: {err}") from err

    async def handle_broadcast_video(call: ServiceCall) -> None:
        entry = _entry_by_id(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        buttons = _with_home_menu_button(_buttons(call.data.get(ATTR_BUTTONS)))
        try:
            await _broadcast_video(
                hass,
                entry,
                file_path=str(call.data[ATTR_FILE_PATH]),
                message=str(call.data.get(ATTR_MESSAGE) or ""),
                user_ids=_parse_user_ids(call.data.get(ATTR_USER_IDS)),
                required_permission=(
                    str(call.data.get(ATTR_REQUIRED_PERMISSION) or "cameras").strip()
                    or "cameras"
                ),
                fmt=_format(call.data.get(ATTR_FORMAT)),
                notify=_to_bool(call.data.get(ATTR_NOTIFY), True),
                buttons=buttons,
                disable_link_preview=_to_bool(
                    call.data.get(ATTR_DISABLE_LINK_PREVIEW), False
                ),
            )
        except Exception as err:
            raise HomeAssistantError(
                f"Не удалось выполнить рассылку видео MAX: {err}"
            ) from err

    async def handle_answer_callback(call: ServiceCall) -> None:
        entry = _entry_by_id(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        try:
            await _api(hass, entry).answer_callback(
                callback_id=str(call.data[ATTR_CALLBACK_ID]),
                text=(
                    str(call.data[ATTR_MESSAGE])
                    if call.data.get(ATTR_MESSAGE) is not None
                    else None
                ),
                fmt=_format(call.data.get(ATTR_FORMAT)),
                buttons=_buttons(call.data.get(ATTR_BUTTONS)),
            )
        except Exception as err:
            raise HomeAssistantError(f"Не удалось ответить на callback MAX: {err}") from err

    common_message_schema = vol.Schema(
        {
            entry_selector,
            vol.Required(ATTR_MESSAGE): cv.string,
            vol.Optional(ATTR_CHAT_ID): vol.Coerce(int),
            vol.Optional(ATTR_USER_ID): vol.Coerce(int),
            vol.Optional(ATTR_FORMAT, default="markdown"): cv.string,
            vol.Optional(ATTR_NOTIFY, default=True): cv.boolean,
            vol.Optional(ATTR_BUTTONS): list,
            vol.Optional(ATTR_DISABLE_LINK_PREVIEW, default=False): cv.boolean,
        }
    )

    edit_message_schema = vol.Schema(
        {
            entry_selector,
            vol.Required(ATTR_MESSAGE_ID): cv.string,
            vol.Required(ATTR_MESSAGE): cv.string,
            vol.Optional(ATTR_FORMAT, default="markdown"): cv.string,
            vol.Optional(ATTR_NOTIFY, default=True): cv.boolean,
            vol.Optional(ATTR_BUTTONS): list,
            vol.Optional(ATTR_EMERGENCY, default=False): cv.boolean,
        }
    )

    send_or_update_schema = vol.Schema(
        {
            entry_selector,
            vol.Required(ATTR_KEY): cv.string,
            vol.Required(ATTR_MESSAGE): cv.string,
            vol.Optional(ATTR_CHAT_ID): vol.Coerce(int),
            vol.Optional(ATTR_USER_ID): vol.Coerce(int),
            vol.Optional(ATTR_EMERGENCY, default=False): cv.boolean,
            vol.Optional(ATTR_FORMAT, default="markdown"): cv.string,
            vol.Optional(ATTR_NOTIFY, default=True): cv.boolean,
            vol.Optional(ATTR_BUTTONS): list,
            vol.Optional(ATTR_DISABLE_LINK_PREVIEW, default=False): cv.boolean,
        }
    )

    send_or_replace_schema = vol.Schema(
        {
            entry_selector,
            vol.Required(ATTR_KEY): cv.string,
            vol.Required(ATTR_MESSAGE): cv.string,
            vol.Optional(ATTR_CHAT_ID): vol.Coerce(int),
            vol.Optional(ATTR_USER_ID): vol.Coerce(int),
            vol.Optional(ATTR_EMERGENCY, default=False): cv.boolean,
            vol.Optional(ATTR_FORMAT, default="markdown"): cv.string,
            vol.Optional(ATTR_NOTIFY, default=True): cv.boolean,
            vol.Optional(ATTR_BUTTONS): list,
            vol.Optional(ATTR_DISABLE_LINK_PREVIEW, default=False): cv.boolean,
        }
    )

    emergency_schema = vol.Schema(
        {
            entry_selector,
            vol.Required(ATTR_MESSAGE): cv.string,
            vol.Optional(ATTR_FORMAT, default="markdown"): cv.string,
            vol.Optional(ATTR_NOTIFY, default=True): cv.boolean,
            vol.Optional(ATTR_BUTTONS): list,
            vol.Optional(ATTR_DISABLE_LINK_PREVIEW, default=False): cv.boolean,
        }
    )

    broadcast_schema = vol.Schema(
        {
            entry_selector,
            vol.Required(ATTR_MESSAGE): cv.string,
            vol.Optional(ATTR_USER_IDS): object,
            vol.Optional(ATTR_REQUIRED_PERMISSION): cv.string,
            vol.Optional(ATTR_FORMAT, default="markdown"): cv.string,
            vol.Optional(ATTR_NOTIFY, default=True): cv.boolean,
            vol.Optional(ATTR_BUTTONS): list,
            vol.Optional(ATTR_DISABLE_LINK_PREVIEW, default=False): cv.boolean,
        }
    )

    broadcast_or_update_schema = vol.Schema(
        {
            entry_selector,
            vol.Required(ATTR_KEY): cv.string,
            vol.Required(ATTR_MESSAGE): cv.string,
            vol.Optional(ATTR_USER_IDS): object,
            vol.Optional(ATTR_REQUIRED_PERMISSION): cv.string,
            vol.Optional(ATTR_FORMAT, default="markdown"): cv.string,
            vol.Optional(ATTR_NOTIFY, default=True): cv.boolean,
            vol.Optional(ATTR_BUTTONS): list,
            vol.Optional(ATTR_DISABLE_LINK_PREVIEW, default=False): cv.boolean,
        }
    )

    image_schema = vol.Schema(
        {
            entry_selector,
            vol.Required(ATTR_FILE_PATH): cv.string,
            vol.Optional(ATTR_MESSAGE, default=""): cv.string,
            vol.Optional(ATTR_CHAT_ID): vol.Coerce(int),
            vol.Optional(ATTR_USER_ID): vol.Coerce(int),
            vol.Optional(ATTR_FORMAT, default="markdown"): cv.string,
            vol.Optional(ATTR_NOTIFY, default=True): cv.boolean,
            vol.Optional(ATTR_BUTTONS): list,
            vol.Optional(ATTR_DISABLE_LINK_PREVIEW, default=False): cv.boolean,
        }
    )

    emergency_image_schema = vol.Schema(
        {
            entry_selector,
            vol.Required(ATTR_FILE_PATH): cv.string,
            vol.Optional(ATTR_MESSAGE, default=""): cv.string,
            vol.Optional(ATTR_FORMAT, default="markdown"): cv.string,
            vol.Optional(ATTR_NOTIFY, default=True): cv.boolean,
            vol.Optional(ATTR_BUTTONS): list,
            vol.Optional(ATTR_DISABLE_LINK_PREVIEW, default=False): cv.boolean,
        }
    )

    broadcast_image_schema = vol.Schema(
        {
            entry_selector,
            vol.Required(ATTR_FILE_PATH): cv.string,
            vol.Optional(ATTR_MESSAGE, default=""): cv.string,
            vol.Optional(ATTR_USER_IDS): object,
            vol.Optional(ATTR_REQUIRED_PERMISSION, default="cameras"): cv.string,
            vol.Optional(ATTR_FORMAT, default="markdown"): cv.string,
            vol.Optional(ATTR_NOTIFY, default=True): cv.boolean,
            vol.Optional(ATTR_BUTTONS): list,
            vol.Optional(ATTR_DISABLE_LINK_PREVIEW, default=False): cv.boolean,
        }
    )

    video_schema = image_schema
    broadcast_video_schema = broadcast_image_schema

    callback_schema = vol.Schema(
        {
            entry_selector,
            vol.Required(ATTR_CALLBACK_ID): cv.string,
            vol.Optional(ATTR_MESSAGE): cv.string,
            vol.Optional(ATTR_FORMAT, default="markdown"): cv.string,
            vol.Optional(ATTR_BUTTONS): list,
        }
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_MESSAGE,
        handle_send_message,
        schema=common_message_schema,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_EDIT_MESSAGE,
        handle_edit_message,
        schema=edit_message_schema,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_OR_UPDATE,
        handle_send_or_update,
        schema=send_or_update_schema,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_OR_REPLACE,
        handle_send_or_replace,
        schema=send_or_replace_schema,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_EMERGENCY,
        handle_send_emergency,
        schema=emergency_schema,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_BROADCAST,
        handle_broadcast,
        schema=broadcast_schema,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_BROADCAST_OR_UPDATE,
        handle_broadcast_or_update,
        schema=broadcast_or_update_schema,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_IMAGE,
        handle_send_image,
        schema=image_schema,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_EMERGENCY_IMAGE,
        handle_send_emergency_image,
        schema=emergency_image_schema,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_BROADCAST_IMAGE,
        handle_broadcast_image,
        schema=broadcast_image_schema,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_VIDEO,
        handle_send_video,
        schema=video_schema,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_BROADCAST_VIDEO,
        handle_broadcast_video,
        schema=broadcast_video_schema,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ANSWER_CALLBACK,
        handle_answer_callback,
        schema=callback_schema,
    )

    async_set_service_schema(
        hass,
        DOMAIN,
        SERVICE_SEND_MESSAGE,
        {
            "fields": {
                ATTR_MESSAGE: {"required": True},
                ATTR_USER_ID: {"required": False},
                ATTR_CHAT_ID: {"required": False},
                ATTR_FORMAT: {"required": False},
                ATTR_NOTIFY: {"required": False},
                ATTR_BUTTONS: {"required": False},
            }
        },
    )

    async_set_service_schema(
        hass,
        DOMAIN,
        SERVICE_EDIT_MESSAGE,
        {
            "fields": {
                ATTR_MESSAGE_ID: {"required": True},
                ATTR_MESSAGE: {"required": True},
                ATTR_FORMAT: {"required": False},
                ATTR_NOTIFY: {"required": False},
                ATTR_BUTTONS: {"required": False},
                ATTR_EMERGENCY: {"required": False},
            }
        },
    )

    async_set_service_schema(
        hass,
        DOMAIN,
        SERVICE_SEND_OR_UPDATE,
        {
            "fields": {
                ATTR_KEY: {"required": True},
                ATTR_MESSAGE: {"required": True},
                ATTR_USER_ID: {"required": False},
                ATTR_CHAT_ID: {"required": False},
                ATTR_EMERGENCY: {"required": False},
                ATTR_FORMAT: {"required": False},
                ATTR_NOTIFY: {"required": False},
                ATTR_BUTTONS: {"required": False},
            }
        },
    )

    async_set_service_schema(
        hass,
        DOMAIN,
        SERVICE_SEND_OR_REPLACE,
        {
            "fields": {
                ATTR_KEY: {"required": True},
                ATTR_MESSAGE: {"required": True},
                ATTR_USER_ID: {"required": False},
                ATTR_CHAT_ID: {"required": False},
                ATTR_EMERGENCY: {"required": False},
                ATTR_FORMAT: {"required": False},
                ATTR_NOTIFY: {"required": False},
                ATTR_BUTTONS: {"required": False},
            }
        },
    )

    async_set_service_schema(
        hass,
        DOMAIN,
        SERVICE_SEND_EMERGENCY,
        {"fields": {ATTR_MESSAGE: {"required": True}}},
    )
    async_set_service_schema(
        hass,
        DOMAIN,
        SERVICE_BROADCAST,
        {"fields": {ATTR_MESSAGE: {"required": True}}},
    )
    async_set_service_schema(
        hass,
        DOMAIN,
        SERVICE_BROADCAST_OR_UPDATE,
        {
            "fields": {
                ATTR_KEY: {"required": True},
                ATTR_MESSAGE: {"required": True},
            }
        },
    )
    async_set_service_schema(
        hass,
        DOMAIN,
        SERVICE_SEND_IMAGE,
        {"fields": {ATTR_FILE_PATH: {"required": True}}},
    )
    async_set_service_schema(
        hass,
        DOMAIN,
        SERVICE_SEND_EMERGENCY_IMAGE,
        {"fields": {ATTR_FILE_PATH: {"required": True}}},
    )
    async_set_service_schema(
        hass,
        DOMAIN,
        SERVICE_BROADCAST_IMAGE,
        {"fields": {ATTR_FILE_PATH: {"required": True}}},
    )
    async_set_service_schema(
        hass,
        DOMAIN,
        SERVICE_SEND_VIDEO,
        {"fields": {ATTR_FILE_PATH: {"required": True}}},
    )
    async_set_service_schema(
        hass,
        DOMAIN,
        SERVICE_BROADCAST_VIDEO,
        {"fields": {ATTR_FILE_PATH: {"required": True}}},
    )
    async_set_service_schema(
        hass,
        DOMAIN,
        SERVICE_ANSWER_CALLBACK,
        {"fields": {ATTR_CALLBACK_ID: {"required": True}}},
    )
