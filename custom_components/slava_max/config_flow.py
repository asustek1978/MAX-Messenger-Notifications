from __future__ import annotations
from copy import deepcopy
from typing import Any
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import OptionsFlowWithReload
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .api import SlavaMaxApi, SlavaMaxApiError, SlavaMaxAuthError
from .const import CONF_ALLOWED_USERS, CONF_EMERGENCY_CHAT_ID, CONF_POLLING, CONF_TARGET_ID, CONF_TARGET_TYPE, CONF_TOKEN, CONF_USERS, CONF_USER_ENABLED, CONF_USER_NAME, CONF_USER_PERMISSIONS, DOMAIN, PERMISSIONS, PERM_ALL, TARGET_CHAT, TARGET_USER
PERMISSION_OPTIONS = [{'value': PERM_ALL, 'label': 'Полный доступ'}, {'value': 'notifications', 'label': 'Получать уведомления'}, {'value': 'lights', 'label': 'Освещение'}, {'value': 'climate', 'label': 'Климат'}, {'value': 'devices', 'label': 'Устройства'}, {'value': 'water', 'label': 'Краны воды'}, {'value': 'vacuum', 'label': 'Робот-пылесос Масяня'}, {'value': 'braga', 'label': 'Braga Controller'}, {'value': 'braga_emergency', 'label': 'Braga: аварийная остановка'}, {'value': 'status', 'label': 'Состояние дома'}, {'value': 'intercom', 'label': 'Домофон'}, {'value': 'intercom_open', 'label': 'Домофон: открытие двери'}, {'value': 'cameras', 'label': 'Камеры'}, {'value': 'scenes', 'label': 'Сценарии дома'}]
PERMISSIONS_SELECTOR = selector.SelectSelector(selector.SelectSelectorConfig(options=PERMISSION_OPTIONS, multiple=True, mode=selector.SelectSelectorMode.LIST))

def _normalize_users(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for raw_id, raw_profile in value.items():
        try:
            user_id = str(int(raw_id))
        except (TypeError, ValueError):
            continue
        profile = raw_profile if isinstance(raw_profile, dict) else {}
        permissions = profile.get(CONF_USER_PERMISSIONS, [])
        if not isinstance(permissions, list):
            permissions = []
        clean_permissions = [item for item in permissions if item == PERM_ALL or item in PERMISSIONS]
        result[user_id] = {CONF_USER_NAME: str(profile.get(CONF_USER_NAME, '')).strip(), CONF_USER_ENABLED: bool(profile.get(CONF_USER_ENABLED, True)), CONF_USER_PERMISSIONS: clean_permissions}
    return result

def _normalize_emergency_chat_id(value: Any) -> int | None:
    if value is None or str(value).strip() == '':
        return None
    return int(str(value).strip())

class SlavaMaxConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return SlavaMaxOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None=None) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            api = SlavaMaxApi(async_get_clientsession(self.hass), token)
            try:
                me = await api.get_me()
            except SlavaMaxAuthError:
                errors['base'] = 'invalid_auth'
            except SlavaMaxApiError:
                errors['base'] = 'cannot_connect'
            else:
                bot_id = str(me.get('user_id', token[-8:]))
                await self.async_set_unique_id(bot_id)
                self._abort_if_unique_id_configured()
                title = me.get('first_name') or me.get('name') or me.get('username') or 'MAX Messenger Notifications'
                data = dict(user_input)
                data[CONF_TOKEN] = token
                data[CONF_TARGET_ID] = int(data[CONF_TARGET_ID])
                try:
                    emergency_chat_id = _normalize_emergency_chat_id(data.get(CONF_EMERGENCY_CHAT_ID))
                except (TypeError, ValueError):
                    errors[CONF_EMERGENCY_CHAT_ID] = 'invalid_chat_id'
                else:
                    if emergency_chat_id is None:
                        data.pop(CONF_EMERGENCY_CHAT_ID, None)
                    else:
                        data[CONF_EMERGENCY_CHAT_ID] = emergency_chat_id
                if errors:
                    return self.async_show_form(step_id='user', data_schema=vol.Schema({vol.Required(CONF_TOKEN): str, vol.Required(CONF_TARGET_TYPE, default=TARGET_USER): vol.In([TARGET_USER, TARGET_CHAT]), vol.Required(CONF_TARGET_ID): int, vol.Required(CONF_POLLING, default=True): bool, vol.Optional(CONF_EMERGENCY_CHAT_ID, default=str(user_input.get(CONF_EMERGENCY_CHAT_ID, '') or '')): str, vol.Optional(CONF_ALLOWED_USERS, default=str(user_input.get(CONF_ALLOWED_USERS, '') or '')): str}), errors=errors)
                return self.async_create_entry(title=f'MAX Messenger Notifications — {title}', data=data)
        return self.async_show_form(step_id='user', data_schema=vol.Schema({vol.Required(CONF_TOKEN): str, vol.Required(CONF_TARGET_TYPE, default=TARGET_USER): vol.In([TARGET_USER, TARGET_CHAT]), vol.Required(CONF_TARGET_ID): int, vol.Required(CONF_POLLING, default=True): bool, vol.Optional(CONF_EMERGENCY_CHAT_ID, default=''): str, vol.Optional(CONF_ALLOWED_USERS, default=''): str}), errors=errors)

class SlavaMaxOptionsFlow(OptionsFlowWithReload):

    def __init__(self) -> None:
        self._selected_user_id: str | None = None
        self._detected_user: dict[str, Any] | None = None

    def _current(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options}

    def _options_copy(self) -> dict[str, Any]:
        return deepcopy(dict(self.config_entry.options))

    def _pending_users(self) -> dict[str, dict[str, Any]]:
        runtime = self.hass.data.get(DOMAIN, {}).get('entries', {}).get(self.config_entry.entry_id, {})
        pending = runtime.get('pending_users', {})
        return pending if isinstance(pending, dict) else {}

    def _users(self) -> dict[str, dict[str, Any]]:
        return _normalize_users(self._current().get(CONF_USERS, {}))

    def _save_options(self, patch: dict[str, Any]) -> config_entries.ConfigFlowResult:
        data = self._options_copy()
        data.update(patch)
        return self.async_create_entry(data=data)

    async def async_step_init(self, user_input: dict[str, Any] | None=None) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(step_id='init', menu_options=['general', 'users'])

    async def async_step_general(self, user_input: dict[str, Any] | None=None) -> config_entries.ConfigFlowResult:
        current = self._current()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = dict(user_input)
            data[CONF_TARGET_ID] = int(data[CONF_TARGET_ID])
            try:
                emergency_chat_id = _normalize_emergency_chat_id(data.get(CONF_EMERGENCY_CHAT_ID))
            except (TypeError, ValueError):
                errors[CONF_EMERGENCY_CHAT_ID] = 'invalid_chat_id'
            else:
                data[CONF_EMERGENCY_CHAT_ID] = emergency_chat_id
                return self._save_options(data)
        return self.async_show_form(step_id='general', data_schema=vol.Schema({vol.Required(CONF_TARGET_TYPE, default=current.get(CONF_TARGET_TYPE, TARGET_USER)): vol.In([TARGET_USER, TARGET_CHAT]), vol.Required(CONF_TARGET_ID, default=current.get(CONF_TARGET_ID, 0)): int, vol.Required(CONF_POLLING, default=current.get(CONF_POLLING, True)): bool, vol.Optional(CONF_EMERGENCY_CHAT_ID, default=str(current.get(CONF_EMERGENCY_CHAT_ID, '') or '')): str, vol.Optional(CONF_ALLOWED_USERS, default=current.get(CONF_ALLOWED_USERS, '')): str}), errors=errors)

    async def async_step_users(self, user_input: dict[str, Any] | None=None) -> config_entries.ConfigFlowResult:
        users = self._users()
        menu = ['add_user']
        if self._pending_users():
            menu.insert(0, 'add_detected_user')
        if users:
            menu.extend(['edit_user', 'delete_user'])
        return self.async_show_menu(step_id='users', menu_options=menu, description_placeholders={'count': str(len(users)), 'users': ', '.join((f'{profile.get(CONF_USER_NAME) or user_id} ({user_id})' for user_id, profile in users.items())) or 'нет'})

    async def async_step_add_detected_user(self, user_input: dict[str, Any] | None=None) -> config_entries.ConfigFlowResult:
        pending = self._pending_users()
        existing = self._users()
        choices = {user_id: f"{item.get('name') or item.get('username') or 'MAX пользователь'} — {user_id}" for user_id, item in pending.items() if user_id not in existing}
        if not choices:
            return await self.async_step_users()
        if user_input is not None:
            user_id = str(user_input['user_id'])
            self._detected_user = deepcopy(pending[user_id])
            self._selected_user_id = user_id
            return await self.async_step_add_detected_user_details()
        return self.async_show_form(step_id='add_detected_user', data_schema=vol.Schema({vol.Required('user_id'): vol.In(choices)}))

    async def async_step_add_detected_user_details(self, user_input: dict[str, Any] | None=None) -> config_entries.ConfigFlowResult:
        if not self._selected_user_id or not self._detected_user:
            return await self.async_step_add_detected_user()
        user_id = self._selected_user_id
        detected = self._detected_user
        default_name = detected.get('name') or detected.get('username') or f'MAX {user_id}'
        if user_input is not None:
            users = self._users()
            permissions = list(user_input.get(CONF_USER_PERMISSIONS, []))
            if PERM_ALL in permissions:
                permissions = [PERM_ALL]
            users[user_id] = {CONF_USER_NAME: str(user_input[CONF_USER_NAME]).strip(), CONF_USER_ENABLED: bool(user_input[CONF_USER_ENABLED]), CONF_USER_PERMISSIONS: permissions}
            return self._save_options({CONF_USERS: users})
        return self.async_show_form(step_id='add_detected_user_details', data_schema=vol.Schema({vol.Required(CONF_USER_NAME, default=default_name): str, vol.Required(CONF_USER_ENABLED, default=True): bool, vol.Required(CONF_USER_PERMISSIONS, default=[]): PERMISSIONS_SELECTOR}), description_placeholders={'user_id': user_id})

    async def async_step_add_user(self, user_input: dict[str, Any] | None=None) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_id = str(int(user_input['user_id']))
            users = self._users()
            if user_id in users:
                errors['user_id'] = 'user_exists'
            else:
                permissions = list(user_input.get(CONF_USER_PERMISSIONS, []))
                if PERM_ALL in permissions:
                    permissions = [PERM_ALL]
                users[user_id] = {CONF_USER_NAME: str(user_input[CONF_USER_NAME]).strip(), CONF_USER_ENABLED: bool(user_input[CONF_USER_ENABLED]), CONF_USER_PERMISSIONS: permissions}
                return self._save_options({CONF_USERS: users})
        return self.async_show_form(step_id='add_user', data_schema=vol.Schema({vol.Required('user_id'): int, vol.Required(CONF_USER_NAME): str, vol.Required(CONF_USER_ENABLED, default=True): bool, vol.Required(CONF_USER_PERMISSIONS, default=[PERM_ALL]): PERMISSIONS_SELECTOR}), errors=errors)

    async def async_step_edit_user(self, user_input: dict[str, Any] | None=None) -> config_entries.ConfigFlowResult:
        users = self._users()
        if not users:
            return await self.async_step_users()
        choices = {user_id: f'{profile.get(CONF_USER_NAME) or user_id} — {user_id}' for user_id, profile in users.items()}
        if user_input is not None:
            self._selected_user_id = str(user_input['user_id'])
            return await self.async_step_edit_user_details()
        return self.async_show_form(step_id='edit_user', data_schema=vol.Schema({vol.Required('user_id'): vol.In(choices)}))

    async def async_step_edit_user_details(self, user_input: dict[str, Any] | None=None) -> config_entries.ConfigFlowResult:
        users = self._users()
        user_id = self._selected_user_id
        if not user_id or user_id not in users:
            return await self.async_step_edit_user()
        profile = users[user_id]
        if user_input is not None:
            permissions = list(user_input.get(CONF_USER_PERMISSIONS, []))
            if PERM_ALL in permissions:
                permissions = [PERM_ALL]
            users[user_id] = {CONF_USER_NAME: str(user_input[CONF_USER_NAME]).strip(), CONF_USER_ENABLED: bool(user_input[CONF_USER_ENABLED]), CONF_USER_PERMISSIONS: permissions}
            return self._save_options({CONF_USERS: users})
        return self.async_show_form(step_id='edit_user_details', data_schema=vol.Schema({vol.Required(CONF_USER_NAME, default=profile.get(CONF_USER_NAME, '')): str, vol.Required(CONF_USER_ENABLED, default=profile.get(CONF_USER_ENABLED, True)): bool, vol.Required(CONF_USER_PERMISSIONS, default=profile.get(CONF_USER_PERMISSIONS, [])): PERMISSIONS_SELECTOR}), description_placeholders={'user_id': user_id})

    async def async_step_delete_user(self, user_input: dict[str, Any] | None=None) -> config_entries.ConfigFlowResult:
        users = self._users()
        if not users:
            return await self.async_step_users()
        choices = {user_id: f'{profile.get(CONF_USER_NAME) or user_id} — {user_id}' for user_id, profile in users.items()}
        if user_input is not None:
            self._selected_user_id = str(user_input['user_id'])
            return await self.async_step_delete_user_confirm()
        return self.async_show_form(step_id='delete_user', data_schema=vol.Schema({vol.Required('user_id'): vol.In(choices)}))

    async def async_step_delete_user_confirm(self, user_input: dict[str, Any] | None=None) -> config_entries.ConfigFlowResult:
        users = self._users()
        user_id = self._selected_user_id
        if not user_id or user_id not in users:
            return await self.async_step_delete_user()
        profile = users[user_id]
        if user_input is not None:
            users.pop(user_id, None)
            return self._save_options({CONF_USERS: users})
        return self.async_show_form(step_id='delete_user_confirm', data_schema=vol.Schema({}), description_placeholders={'user_id': user_id, 'name': profile.get(CONF_USER_NAME) or user_id})
