from __future__ import annotations
import asyncio
import logging
import mimetypes
from contextlib import suppress
from pathlib import Path
from typing import Any
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .api import SlavaMaxApi, SlavaMaxApiError
from .const import CONF_ALLOWED_USERS, CONF_EMERGENCY_CHAT_ID, CONF_POLLING, CONF_TARGET_ID, CONF_TARGET_TYPE, CONF_TOKEN, CONF_USERS, CONF_USER_ENABLED, CONF_USER_NAME, CONF_USER_PERMISSIONS, DOMAIN, EVENT_ACCESS_REQUEST, EVENT_NAME, PERM_ALL, PERM_NOTIFICATIONS, SERVICE_ANSWER_CALLBACK, SERVICE_BROADCAST, SERVICE_BROADCAST_IMAGE, SERVICE_BROADCAST_VIDEO, SERVICE_SEND_EMERGENCY, SERVICE_SEND_EMERGENCY_IMAGE, SERVICE_SEND_IMAGE, SERVICE_SEND_VIDEO, SERVICE_SEND_MESSAGE
_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.NOTIFY]
ATTR_CONFIG_ENTRY_ID = 'config_entry_id'
ATTR_TEXT = 'message'
ATTR_FILE_PATH = 'file_path'
ATTR_CHAT_ID = 'chat_id'
ATTR_USER_ID = 'user_id'
ATTR_USER_IDS = 'user_ids'
ATTR_REQUIRED_PERMISSION = 'required_permission'
ATTR_FORMAT = 'format'
ATTR_NOTIFY = 'notify'
ATTR_BUTTONS = 'buttons'
ATTR_DISABLE_LINK_PREVIEW = 'disable_link_preview'
ATTR_CALLBACK_ID = 'callback_id'
HOME_MENU_PAYLOAD = 'home_main'
HOME_MENU_BUTTON = {'type': 'callback', 'text': '🏠 Управление домом', 'payload': HOME_MENU_PAYLOAD}

def _with_home_menu_button(buttons: list[list[dict[str, Any]]] | None) -> list[list[dict[str, Any]]]:
    """Return buttons with a bottom Home control button.

    The button is appended only once. This helper is used for notification
    broadcasts, while direct bot/menu messages remain unchanged.
    """
    result: list[list[dict[str, Any]]] = []
    for row in buttons or []:
        if isinstance(row, list):
            result.append([dict(button) for button in row if isinstance(button, dict)])
    for row in result:
        for button in row:
            if str(button.get('payload', '')) == HOME_MENU_PAYLOAD:
                return result
    result.append([dict(HOME_MENU_BUTTON)])
    return result
SEND_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string, vol.Required(ATTR_TEXT): cv.string, vol.Optional(ATTR_CHAT_ID): vol.Coerce(int), vol.Optional(ATTR_USER_ID): vol.Coerce(int), vol.Optional(ATTR_FORMAT, default='markdown'): vol.In(['markdown', 'html', 'plain']), vol.Optional(ATTR_NOTIFY, default=True): cv.boolean, vol.Optional(ATTR_BUTTONS): list, vol.Optional(ATTR_DISABLE_LINK_PREVIEW, default=False): cv.boolean})
EMERGENCY_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string, vol.Required(ATTR_TEXT): cv.string, vol.Optional(ATTR_FORMAT, default='markdown'): vol.In(['markdown', 'html', 'plain']), vol.Optional(ATTR_NOTIFY, default=True): cv.boolean, vol.Optional(ATTR_BUTTONS): list, vol.Optional(ATTR_DISABLE_LINK_PREVIEW, default=False): cv.boolean})
EMERGENCY_IMAGE_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string, vol.Required(ATTR_FILE_PATH): cv.string, vol.Optional(ATTR_TEXT, default=''): cv.string, vol.Optional(ATTR_FORMAT, default='markdown'): vol.In(['markdown', 'html', 'plain']), vol.Optional(ATTR_NOTIFY, default=True): cv.boolean, vol.Optional(ATTR_BUTTONS): list, vol.Optional(ATTR_DISABLE_LINK_PREVIEW, default=False): cv.boolean})
BROADCAST_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string, vol.Required(ATTR_TEXT): cv.string, vol.Optional(ATTR_USER_IDS): [vol.Coerce(int)], vol.Optional(ATTR_REQUIRED_PERMISSION): cv.string, vol.Optional(ATTR_FORMAT, default='markdown'): vol.In(['markdown', 'html', 'plain']), vol.Optional(ATTR_NOTIFY, default=True): cv.boolean, vol.Optional(ATTR_BUTTONS): list, vol.Optional(ATTR_DISABLE_LINK_PREVIEW, default=False): cv.boolean})
SEND_IMAGE_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string, vol.Required(ATTR_FILE_PATH): cv.string, vol.Optional(ATTR_TEXT, default=''): cv.string, vol.Optional(ATTR_CHAT_ID): vol.Coerce(int), vol.Optional(ATTR_USER_ID): vol.Coerce(int), vol.Optional(ATTR_FORMAT, default='markdown'): vol.In(['markdown', 'html', 'plain']), vol.Optional(ATTR_NOTIFY, default=True): cv.boolean, vol.Optional(ATTR_BUTTONS): list, vol.Optional(ATTR_DISABLE_LINK_PREVIEW, default=False): cv.boolean})
BROADCAST_IMAGE_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string, vol.Required(ATTR_FILE_PATH): cv.string, vol.Optional(ATTR_TEXT, default=''): cv.string, vol.Optional(ATTR_USER_IDS): [vol.Coerce(int)], vol.Optional(ATTR_REQUIRED_PERMISSION): cv.string, vol.Optional(ATTR_FORMAT, default='markdown'): vol.In(['markdown', 'html', 'plain']), vol.Optional(ATTR_NOTIFY, default=True): cv.boolean, vol.Optional(ATTR_BUTTONS): list, vol.Optional(ATTR_DISABLE_LINK_PREVIEW, default=False): cv.boolean})
SEND_VIDEO_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string, vol.Required(ATTR_FILE_PATH): cv.string, vol.Optional(ATTR_TEXT, default=''): cv.string, vol.Optional(ATTR_CHAT_ID): vol.Coerce(int), vol.Optional(ATTR_USER_ID): vol.Coerce(int), vol.Optional(ATTR_FORMAT, default='markdown'): vol.In(['markdown', 'html', 'plain']), vol.Optional(ATTR_NOTIFY, default=True): cv.boolean, vol.Optional(ATTR_BUTTONS): list, vol.Optional(ATTR_DISABLE_LINK_PREVIEW, default=False): cv.boolean})
BROADCAST_VIDEO_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string, vol.Required(ATTR_FILE_PATH): cv.string, vol.Optional(ATTR_TEXT, default=''): cv.string, vol.Optional(ATTR_USER_IDS): [vol.Coerce(int)], vol.Optional(ATTR_REQUIRED_PERMISSION): cv.string, vol.Optional(ATTR_FORMAT, default='markdown'): vol.In(['markdown', 'html', 'plain']), vol.Optional(ATTR_NOTIFY, default=True): cv.boolean, vol.Optional(ATTR_BUTTONS): list, vol.Optional(ATTR_DISABLE_LINK_PREVIEW, default=False): cv.boolean})
ANSWER_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string, vol.Required(ATTR_CALLBACK_ID): cv.string, vol.Optional(ATTR_TEXT): cv.string, vol.Optional(ATTR_FORMAT, default='markdown'): vol.In(['markdown', 'html', 'plain']), vol.Optional(ATTR_BUTTONS): list})

def settings(entry: ConfigEntry) -> dict[str, Any]:
    return {**entry.data, **entry.options}

def _legacy_allowed_users(value: str) -> set[int]:
    result: set[int] = set()
    for item in (value or '').replace(';', ',').split(','):
        item = item.strip()
        if not item:
            continue
        try:
            result.add(int(item))
        except ValueError:
            _LOGGER.warning('Ignored invalid allowed MAX user id: %s', item)
    return result

def _user_profiles(cfg: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Build ACL profiles with legacy compatibility."""
    result: dict[int, dict[str, Any]] = {}
    raw_users = cfg.get(CONF_USERS, {})
    if isinstance(raw_users, dict):
        for raw_id, raw_profile in raw_users.items():
            try:
                user_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            profile = raw_profile if isinstance(raw_profile, dict) else {}
            permissions = profile.get(CONF_USER_PERMISSIONS, [])
            if not isinstance(permissions, list):
                permissions = []
            result[user_id] = {CONF_USER_NAME: str(profile.get(CONF_USER_NAME, '')).strip(), CONF_USER_ENABLED: bool(profile.get(CONF_USER_ENABLED, True)), CONF_USER_PERMISSIONS: [str(item) for item in permissions]}
    for user_id in _legacy_allowed_users(str(cfg.get(CONF_ALLOWED_USERS, '') or '')):
        result.setdefault(user_id, {CONF_USER_NAME: '', CONF_USER_ENABLED: True, CONF_USER_PERMISSIONS: [PERM_ALL]})
    return result

def _access_for_user(profiles: dict[int, dict[str, Any]], user_id: Any) -> tuple[bool, dict[str, Any] | None]:
    if not profiles:
        return (True, None)
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return (False, None)
    profile = profiles.get(uid)
    if profile is None:
        return (False, None)
    return (bool(profile.get(CONF_USER_ENABLED, True)), profile)

def _notification_recipients(profiles: dict[int, dict[str, Any]], explicit_user_ids: list[int] | None=None, required_permission: str | None=None) -> list[int]:
    explicit = set(explicit_user_ids or [])
    result: list[int] = []
    for user_id, profile in profiles.items():
        if not profile.get(CONF_USER_ENABLED, True):
            continue
        if explicit and user_id not in explicit:
            continue
        permissions = set(profile.get(CONF_USER_PERMISSIONS, []))
        has_notifications = PERM_ALL in permissions or PERM_NOTIFICATIONS in permissions
        if not has_notifications:
            continue
        if required_permission and PERM_ALL not in permissions and (required_permission not in permissions):
            continue
        result.append(user_id)
    return sorted(result)

def _entry_runtime(hass: HomeAssistant, entry_id: str | None) -> dict[str, Any]:
    entries = hass.data.get(DOMAIN, {}).get('entries', {})
    if entry_id:
        runtime = entries.get(entry_id)
        if runtime is None:
            raise HomeAssistantError(f'MAX Messenger Notifications config entry not found: {entry_id}')
        return runtime
    if len(entries) == 1:
        return next(iter(entries.values()))
    if not entries:
        raise HomeAssistantError('MAX Messenger Notifications is not configured')
    raise HomeAssistantError('Several MAX Messenger Notifications entries exist; specify config_entry_id')

def _event_data(update: dict[str, Any]) -> dict[str, Any]:
    update_type = update.get('update_type') or 'unknown'
    callback = update.get('callback') or {}
    message = update.get('message') or callback.get('message') or {}
    user = update.get('user') or callback.get('user') or message.get('sender') or message.get('user') or {}
    body = message.get('body') or {}
    text = body.get('text') or message.get('text') or update.get('text') or ''
    command = None
    args = ''
    if isinstance(text, str) and text.startswith('/'):
        raw = text[1:].strip()
        if raw:
            parts = raw.split(maxsplit=1)
            command = parts[0].split('@', 1)[0].lower()
            if len(parts) > 1:
                args = parts[1]
    recipient = message.get('recipient') or {}
    return {'type': 'callback' if update_type == 'message_callback' else 'message' if update_type == 'message_created' else update_type, 'update_type': update_type, 'timestamp': update.get('timestamp'), 'chat_id': update.get('chat_id') or message.get('chat_id') or recipient.get('chat_id'), 'user_id': user.get('user_id') or user.get('id'), 'username': user.get('username'), 'name': user.get('first_name') or user.get('name'), 'text': text, 'command': command, 'args': args, 'callback_id': callback.get('callback_id'), 'payload': callback.get('payload') or callback.get('data') or update.get('payload'), 'message_id': message.get('message_id') or message.get('id') or callback.get('message_id'), 'raw': update}

async def _poll_loop(hass: HomeAssistant, entry: ConfigEntry, api: SlavaMaxApi) -> None:
    marker: int | None = None
    initialized = False
    while True:
        try:
            profiles = _user_profiles(settings(entry))
            result = await api.get_updates(marker, timeout=30)
            next_marker = result.get('marker')
            if not initialized:
                marker = next_marker
                initialized = True
                continue
            for update in result.get('updates', []):
                data = _event_data(update)
                user_id = data.get('user_id')
                authorized, profile = _access_for_user(profiles, user_id)
                if not authorized:
                    _LOGGER.warning('Blocked MAX event from non-allowed user_id=%s', user_id)
                    try:
                        pending_user_id = int(user_id)
                    except (TypeError, ValueError):
                        pending_user_id = None
                    if pending_user_id is not None:
                        runtime = hass.data.get(DOMAIN, {}).get('entries', {}).get(entry.entry_id)
                        if runtime is not None:
                            runtime.setdefault('pending_users', {})[str(pending_user_id)] = {'user_id': pending_user_id, 'name': data.get('name') or '', 'username': data.get('username') or ''}
                        hass.bus.async_fire(EVENT_ACCESS_REQUEST, {'config_entry_id': entry.entry_id, 'user_id': pending_user_id, 'name': data.get('name') or '', 'username': data.get('username') or ''})
                    continue
                permissions = list(profile.get(CONF_USER_PERMISSIONS, [])) if profile is not None else [PERM_ALL]
                data['config_entry_id'] = entry.entry_id
                data['authorized'] = True
                data['permissions'] = permissions
                data['access_name'] = profile.get(CONF_USER_NAME, '') if profile is not None else ''
                hass.bus.async_fire(EVENT_NAME, data)
            if next_marker is not None:
                marker = next_marker
        except asyncio.CancelledError:
            raise
        except SlavaMaxApiError as err:
            _LOGGER.warning('MAX polling error: %s', err)
            await asyncio.sleep(10)
        except Exception:
            _LOGGER.exception('Unexpected MAX polling error')
            await asyncio.sleep(10)

def _fmt(call: ServiceCall) -> str | None:
    fmt = call.data.get(ATTR_FORMAT)
    return None if fmt == 'plain' else fmt

async def _read_image(hass: HomeAssistant, file_path: str) -> tuple[bytes, str, str]:
    path = Path(file_path).expanduser()
    if not path.is_absolute():
        raise HomeAssistantError('file_path должен быть абсолютным путём')
    exists = await hass.async_add_executor_job(path.is_file)
    if not exists:
        raise HomeAssistantError(f'Файл изображения не найден: {file_path}')
    try:
        data = await hass.async_add_executor_job(path.read_bytes)
    except OSError as err:
        raise HomeAssistantError(f'Не удалось прочитать изображение: {err}') from err
    if not data:
        raise HomeAssistantError(f'Файл изображения пустой: {file_path}')
    content_type = mimetypes.guess_type(path.name)[0] or 'image/jpeg'
    if not content_type.startswith('image/'):
        raise HomeAssistantError(f'Неподдерживаемый тип изображения: {content_type}')
    return (data, path.name, content_type)

async def _read_video(hass: HomeAssistant, file_path: str) -> tuple[bytes, str, str]:
    path = Path(file_path).expanduser()
    if not path.is_absolute():
        raise HomeAssistantError('file_path должен быть абсолютным путём')
    exists = await hass.async_add_executor_job(path.is_file)
    if not exists:
        raise HomeAssistantError(f'Файл видео не найден: {file_path}')
    try:
        size = await hass.async_add_executor_job(lambda: path.stat().st_size)
    except OSError as err:
        raise HomeAssistantError(f'Не удалось проверить видео: {err}') from err
    if size <= 0:
        raise HomeAssistantError(f'Файл видео пустой: {file_path}')
    if size > 250 * 1024 * 1024:
        raise HomeAssistantError('MAX поддерживает видео размером не более 250 МБ')
    content_type = mimetypes.guess_type(path.name)[0] or 'video/mp4'
    allowed_suffixes = {'.mp4', '.mov', '.mkv', '.webm'}
    if path.suffix.lower() not in allowed_suffixes:
        raise HomeAssistantError('MAX поддерживает видео MP4, MOV, MKV и WEBM')
    try:
        data = await hass.async_add_executor_job(path.read_bytes)
    except OSError as err:
        raise HomeAssistantError(f'Не удалось прочитать видео: {err}') from err
    return (data, path.name, content_type)

def _resolve_single_target(call: ServiceCall, cfg: dict[str, Any]) -> tuple[str, int]:
    chat_id = call.data.get(ATTR_CHAT_ID)
    user_id = call.data.get(ATTR_USER_ID)
    if chat_id is not None and user_id is not None:
        raise HomeAssistantError('Specify only one of chat_id or user_id')
    if chat_id is not None:
        return ('chat_id', int(chat_id))
    if user_id is not None:
        return ('user_id', int(user_id))
    return (str(cfg[CONF_TARGET_TYPE]), int(cfg[CONF_TARGET_ID]))

def _emergency_chat_id(cfg: dict[str, Any]) -> int:
    value = cfg.get(CONF_EMERGENCY_CHAT_ID)
    if value is None or str(value).strip() == '':
        raise HomeAssistantError('Аварийный канал MAX не настроен. Откройте настройки MAX Messenger Notifications и укажите ID аварийного канала.')
    try:
        return int(value)
    except (TypeError, ValueError) as err:
        raise HomeAssistantError('Некорректный ID аварийного канала MAX') from err

def _broadcast_targets(cfg: dict[str, Any], call: ServiceCall) -> list[tuple[str, int]]:
    profiles = _user_profiles(cfg)
    explicit_user_ids = call.data.get(ATTR_USER_IDS)
    required_permission = str(call.data.get(ATTR_REQUIRED_PERMISSION) or '').strip() or None
    recipients = _notification_recipients(profiles, explicit_user_ids=explicit_user_ids, required_permission=required_permission)
    if not profiles:
        return [(str(cfg[CONF_TARGET_TYPE]), int(cfg[CONF_TARGET_ID]))]
    return [('user_id', user_id) for user_id in recipients]

async def _register_services(hass: HomeAssistant) -> None:
    """Register domain services once.

    Registration does not perform any network request, so MAX cannot
    delay Home Assistant startup.
    """

    async def send_message(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        api: SlavaMaxApi = runtime['api']
        entry: ConfigEntry = runtime['entry']
        cfg = settings(entry)
        target_type, target_id = _resolve_single_target(call, cfg)
        try:
            await api.send_message(text=call.data[ATTR_TEXT], target_type=target_type, target_id=target_id, fmt=_fmt(call), notify=call.data[ATTR_NOTIFY], buttons=call.data.get(ATTR_BUTTONS), disable_link_preview=call.data[ATTR_DISABLE_LINK_PREVIEW])
        except SlavaMaxApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def send_emergency(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        api: SlavaMaxApi = runtime['api']
        entry: ConfigEntry = runtime['entry']
        target_id = _emergency_chat_id(settings(entry))
        try:
            await api.send_message(text=call.data[ATTR_TEXT], target_type='chat_id', target_id=target_id, fmt=_fmt(call), notify=call.data[ATTR_NOTIFY], buttons=call.data.get(ATTR_BUTTONS), disable_link_preview=call.data[ATTR_DISABLE_LINK_PREVIEW])
        except SlavaMaxApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def broadcast_message(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        api: SlavaMaxApi = runtime['api']
        entry: ConfigEntry = runtime['entry']
        targets = _broadcast_targets(settings(entry), call)
        if not targets:
            _LOGGER.info('Slava MAX broadcast skipped: no eligible recipients')
            return
        try:
            for target_type, target_id in targets:
                await api.send_message(text=call.data[ATTR_TEXT], target_type=target_type, target_id=target_id, fmt=_fmt(call), notify=call.data[ATTR_NOTIFY], buttons=_with_home_menu_button(call.data.get(ATTR_BUTTONS)), disable_link_preview=call.data[ATTR_DISABLE_LINK_PREVIEW])
        except SlavaMaxApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def send_image(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        api: SlavaMaxApi = runtime['api']
        entry: ConfigEntry = runtime['entry']
        cfg = settings(entry)
        target_type, target_id = _resolve_single_target(call, cfg)
        data, filename, content_type = await _read_image(hass, call.data[ATTR_FILE_PATH])
        try:
            token = await api.upload_image(data=data, filename=filename, content_type=content_type)
            await api.send_image_token(token=token, text=call.data[ATTR_TEXT], target_type=target_type, target_id=target_id, fmt=_fmt(call), notify=call.data[ATTR_NOTIFY], buttons=call.data.get(ATTR_BUTTONS), disable_link_preview=call.data[ATTR_DISABLE_LINK_PREVIEW])
        except SlavaMaxApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def send_emergency_image(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        api: SlavaMaxApi = runtime['api']
        entry: ConfigEntry = runtime['entry']
        target_id = _emergency_chat_id(settings(entry))
        data, filename, content_type = await _read_image(hass, call.data[ATTR_FILE_PATH])
        try:
            token = await api.upload_image(data=data, filename=filename, content_type=content_type)
            await api.send_image_token(token=token, text=call.data[ATTR_TEXT], target_type='chat_id', target_id=target_id, fmt=_fmt(call), notify=call.data[ATTR_NOTIFY], buttons=call.data.get(ATTR_BUTTONS), disable_link_preview=call.data[ATTR_DISABLE_LINK_PREVIEW])
        except SlavaMaxApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def broadcast_image(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        api: SlavaMaxApi = runtime['api']
        entry: ConfigEntry = runtime['entry']
        targets = _broadcast_targets(settings(entry), call)
        if not targets:
            _LOGGER.info('Slava MAX image broadcast skipped: no eligible recipients')
            return
        data, filename, content_type = await _read_image(hass, call.data[ATTR_FILE_PATH])
        try:
            token = await api.upload_image(data=data, filename=filename, content_type=content_type)
            for target_type, target_id in targets:
                await api.send_image_token(token=token, text=call.data[ATTR_TEXT], target_type=target_type, target_id=target_id, fmt=_fmt(call), notify=call.data[ATTR_NOTIFY], buttons=_with_home_menu_button(call.data.get(ATTR_BUTTONS)), disable_link_preview=call.data[ATTR_DISABLE_LINK_PREVIEW])
        except SlavaMaxApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def send_video(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        api: SlavaMaxApi = runtime['api']
        entry: ConfigEntry = runtime['entry']
        cfg = settings(entry)
        target_type, target_id = _resolve_single_target(call, cfg)
        data, filename, content_type = await _read_video(hass, call.data[ATTR_FILE_PATH])
        try:
            token = await api.upload_video(data=data, filename=filename, content_type=content_type)
            await api.send_video_token(token=token, text=call.data[ATTR_TEXT], target_type=target_type, target_id=target_id, fmt=_fmt(call), notify=call.data[ATTR_NOTIFY], buttons=call.data.get(ATTR_BUTTONS), disable_link_preview=call.data[ATTR_DISABLE_LINK_PREVIEW])
        except SlavaMaxApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def broadcast_video(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        api: SlavaMaxApi = runtime['api']
        entry: ConfigEntry = runtime['entry']
        targets = _broadcast_targets(settings(entry), call)
        if not targets:
            _LOGGER.info('Slava MAX video broadcast skipped: no eligible recipients')
            return
        data, filename, content_type = await _read_video(hass, call.data[ATTR_FILE_PATH])
        try:
            token = await api.upload_video(data=data, filename=filename, content_type=content_type)
            for target_type, target_id in targets:
                await api.send_video_token(token=token, text=call.data[ATTR_TEXT], target_type=target_type, target_id=target_id, fmt=_fmt(call), notify=call.data[ATTR_NOTIFY], buttons=_with_home_menu_button(call.data.get(ATTR_BUTTONS)), disable_link_preview=call.data[ATTR_DISABLE_LINK_PREVIEW])
        except SlavaMaxApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def answer_callback(call: ServiceCall) -> None:
        runtime = _entry_runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        api: SlavaMaxApi = runtime['api']
        try:
            await api.answer_callback(callback_id=call.data[ATTR_CALLBACK_ID], text=call.data.get(ATTR_TEXT), fmt=_fmt(call), buttons=call.data.get(ATTR_BUTTONS))
        except SlavaMaxApiError as err:
            raise HomeAssistantError(str(err)) from err
    service_definitions = ((SERVICE_SEND_MESSAGE, send_message, SEND_SCHEMA), (SERVICE_SEND_EMERGENCY, send_emergency, EMERGENCY_SCHEMA), (SERVICE_BROADCAST, broadcast_message, BROADCAST_SCHEMA), (SERVICE_SEND_IMAGE, send_image, SEND_IMAGE_SCHEMA), (SERVICE_SEND_EMERGENCY_IMAGE, send_emergency_image, EMERGENCY_IMAGE_SCHEMA), (SERVICE_BROADCAST_IMAGE, broadcast_image, BROADCAST_IMAGE_SCHEMA), (SERVICE_SEND_VIDEO, send_video, SEND_VIDEO_SCHEMA), (SERVICE_BROADCAST_VIDEO, broadcast_video, BROADCAST_VIDEO_SCHEMA), (SERVICE_ANSWER_CALLBACK, answer_callback, ANSWER_SCHEMA))
    for service_name, handler, schema in service_definitions:
        if hass.services.has_service(DOMAIN, service_name):
            continue
        hass.services.async_register(DOMAIN, service_name, handler, schema=schema)

async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault('entries', {})
    await _register_services(hass)
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MAX Messenger Notifications without blocking HA startup on MAX API."""
    if entry.title.startswith('Slava MAX'):
        hass.config_entries.async_update_entry(entry, title=entry.title.replace('Slava MAX', 'MAX Messenger Notifications', 1))
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault('entries', {})
    await _register_services(hass)
    api = SlavaMaxApi(async_get_clientsession(hass), entry.data[CONF_TOKEN])
    runtime: dict[str, Any] = {'entry': entry, 'api': api, 'poll_task': None, 'pending_users': {}}
    hass.data[DOMAIN]['entries'][entry.entry_id] = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if settings(entry).get(CONF_POLLING, True):
        runtime['poll_task'] = hass.async_create_background_task(_poll_loop(hass, entry, api), f'slava_max_poll_{entry.entry_id}')
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    runtime = hass.data.get(DOMAIN, {}).get('entries', {}).pop(entry.entry_id, None)
    if runtime and runtime.get('poll_task'):
        task = runtime['poll_task']
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    return True
