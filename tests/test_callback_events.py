"""Offline event-contract and callback-authorization regression tests.

The real integration functions are compiled without HA-only imports. Synthetic
updates follow MAX's MessageCallbackUpdate shape; no API calls are made.
Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import ast
import copy
import logging
import os
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "slava_max"
SOURCE = Path(os.environ.get("MAX_TEST_SOURCE", COMPONENT / "__init__.py"))


def load_functions():
    names = {
        "_handle_update", "_update_message", "_update_user", "_extract_user",
        "_event_type", "_callback_payload", "_message_text", "_extract_message_id",
        "_conf", "_configured_users", "_permissions", "_is_allowed", "_to_bool",
    }
    tree = ast.parse(SOURCE.read_text())
    body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    body.extend(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names)
    module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
    namespace = runpy.run_path(str(COMPONENT / "const.py"))
    namespace["_LOGGER"] = logging.getLogger("max_callback_test")
    exec(compile(module, str(SOURCE), "exec"), namespace)
    # Produce the same profile representation used by the actual options flow.
    config_source = COMPONENT / "config_flow.py"
    config_tree = ast.parse(config_source.read_text())
    normalizer = next(n for n in config_tree.body if isinstance(n, ast.FunctionDef) and n.name == "_normalize_users")
    config_module = ast.fix_missing_locations(ast.Module(body=[body[0], normalizer], type_ignores=[]))
    exec(compile(config_module, str(config_source), "exec"), namespace)
    return namespace


def callback_update():
    return {
        "update_type": "message_callback",
        "timestamp": 1700000000000,
        "user_locale": "ru",
        "callback": {
            "callback_id": "callback-test",
            "payload": "TEST::CONFIRM::record-1",
            "user": {"user_id": 1001, "first_name": "Test actor", "username": "test_actor"},
        },
        "message": {
            "sender": {"user_id": 9000, "first_name": "Test bot", "is_bot": True},
            "recipient": {"user_id": 2002, "chat_id": -3003},
            "body": {"mid": "mid.test", "text": "Test reminder"},
        },
    }


class CallbackTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_functions()
        self.events = []
        self.hass = SimpleNamespace(bus=SimpleNamespace(
            async_fire=lambda name, data: self.events.append((name, data))))
        self.entry = SimpleNamespace(entry_id="entry-test", options={}, data={
            "users": self.ns["_normalize_users"]({"1001": {
                "name": "Configured actor", "enabled": True, "permissions": ["*"],
            }}),
        })

    def test_options_flow_profiles_support_multiple_users(self):
        self.entry.data["users"] = self.ns["_normalize_users"]({
            "1001": {"name": "First", "enabled": True, "permissions": ["*"]},
            "2002": {"name": "Second", "enabled": True, "permissions": ["notifications"]},
        })
        self.assertIsInstance(self.entry.data["users"], dict)
        self.assertEqual(set(self.ns["_configured_users"](self.entry.data)), {1001, 2002})
        for user_id in (1001, 2002):
            with self.subTest(user_id=user_id):
                update = callback_update()
                update["callback"]["user"]["user_id"] = user_id
                self.assertEqual(self.event_data(update)["user_id"], user_id)

    def test_general_settings_allowlist_string_keeps_complete_ids(self):
        self.entry.data = {"allowed_users": "1001, 2002; 3003, invalid,,"}
        self.assertEqual(set(self.ns["_configured_users"](self.entry.data)), {1001, 2002, 3003})
        self.assertEqual(self.event_data(callback_update())["user_id"], 1001)
        for single_digit in (0, 1, 2, 3):
            self.assertFalse(self.ns["_is_allowed"](self.entry.data, single_digit))

    def test_explicit_dict_denials_override_legacy_allowlist(self):
        for enabled, permissions in ((False, ["*"]), (True, [])):
            with self.subTest(enabled=enabled, permissions=permissions):
                self.entry.data = {"allowed_users": "1001", "users": self.ns["_normalize_users"]({
                    "1001": {"name": "Denied", "enabled": enabled, "permissions": permissions},
                })}
                self.assertEqual([name for name, _ in self.dispatch(callback_update())], ["slava_max_access_request"])

    def test_dict_options_override_data_profiles(self):
        self.entry.options = {"users": self.ns["_normalize_users"]({
            "1001": {"enabled": False, "permissions": ["*"]},
        })}
        self.assertEqual([name for name, _ in self.dispatch(callback_update())], ["slava_max_access_request"])

    def test_dict_key_is_authoritative_over_embedded_id(self):
        self.entry.data = {"users": {"1001": {"user_id": 9999, "enabled": True, "permissions": ["*"]}}}
        self.assertEqual(set(self.ns["_configured_users"](self.entry.data)), {1001})
        self.assertFalse(self.ns["_is_allowed"](self.entry.data, 9999))

    def test_legacy_list_profiles_still_work(self):
        self.entry.data = {"users": [{"user_id": 1001, "name": "Legacy", "enabled": True, "permissions": ["*"]}]}
        self.assertEqual(self.event_data(callback_update())["access_name"], "Legacy")

    def dispatch(self, update):
        self.events.clear()
        self.ns["_handle_update"](self.hass, self.entry, update)
        return self.events

    def event_data(self, update):
        events = self.dispatch(update)
        self.assertEqual([name for name, _ in events], ["slava_max_event"])
        return events[0][1]

    def test_pressing_user_wins_over_bot_and_recipient(self):
        update = callback_update()
        original = copy.deepcopy(update)
        event = self.event_data(update)
        self.assertEqual(event["user_id"], 1001)
        self.assertEqual(event["user_name"], "Test actor")
        self.assertEqual(event["username"], "test_actor")
        self.assertEqual(event["access_name"], "Configured actor")
        self.assertTrue(event["authorized"])
        self.assertEqual(event["permissions"], ["*"])
        self.assertEqual(event["chat_id"], -3003)
        self.assertEqual(event["message_id"], "mid.test")
        self.assertEqual(event["text"], "Test reminder")
        self.assertEqual(event["callback_id"], "callback-test")
        self.assertEqual(event["config_entry_id"], "entry-test")
        self.assertEqual(event["payload"], "TEST::CONFIRM::record-1")
        self.assertEqual(event["raw"], original)
        self.assertEqual(update, original)

    def test_legacy_and_v077_event_consumers_both_receive_callback(self):
        update = callback_update()
        # Isolate the event-contract regression from the actor-selection bug.
        update["message"]["sender"] = update["callback"]["user"].copy()
        event = self.event_data(update)
        self.assertEqual(event.get("type"), "callback")
        self.assertEqual(event.get("event_type"), "callback")
        self.assertEqual(event["update_type"], "message_callback")
        self.assertEqual(event["timestamp"], 1700000000000)

    def test_unauthorized_actor_cannot_borrow_sender_or_recipient_access(self):
        update = callback_update()
        update["callback"]["user"]["user_id"] = 9999
        update["message"]["sender"]["user_id"] = 1001
        update["message"]["recipient"]["user_id"] = 1001
        events = self.dispatch(update)
        self.assertEqual([name for name, _ in events], ["slava_max_access_request"])
        self.assertEqual(events[0][1]["user_id"], 9999)

    def test_missing_or_invalid_actor_never_uses_other_identity(self):
        for user in (None, {}, "1001", {"user_id": True}, {"user_id": 1001.5},
                     {"user_id": "invalid"}, {"user_id": []}):
            with self.subTest(user=user):
                update = callback_update()
                update["callback"]["user"] = user
                update["message"]["sender"]["user_id"] = 1001
                update["message"]["recipient"]["user_id"] = 1001
                update["user"] = {"user_id": 1001}
                update["user_locale"] = {"user_id": 1001}
                self.assertEqual(self.dispatch(update), [])

    def test_disabled_and_permissionless_users_stay_blocked(self):
        for enabled, permissions in ((False, ["*"]), (True, [])):
            with self.subTest(enabled=enabled, permissions=permissions):
                self.entry.options = {"users": [{"user_id": 1001, "enabled": enabled, "permissions": permissions}]}
                self.assertEqual([name for name, _ in self.dispatch(callback_update())], ["slava_max_access_request"])

    def test_configured_permissions_are_not_elevated(self):
        self.entry.options = {"users": [{"user_id": 1001, "enabled": True, "permissions": ["notifications"]}]}
        event = self.event_data(callback_update())
        self.assertEqual(event["permissions"], ["notifications"])
        self.assertFalse(self.ns["_is_allowed"](self.ns["_conf"](self.entry), 1001, "control_lights"))

    def test_legacy_allowlist_accepts_string_actor_id(self):
        self.entry.data = {"allowed_users": ["1001"]}
        update = callback_update()
        update["callback"]["user"]["user_id"] = "1001"
        self.assertEqual(self.event_data(update)["user_id"], 1001)

    def test_callback_without_original_message_still_works(self):
        update = callback_update()
        update["message"] = None
        event = self.event_data(update)
        self.assertEqual(event["type"], "callback")
        self.assertEqual(event["callback_id"], "callback-test")
        self.assertIsNone(event["message_id"])

    def test_nested_message_and_legacy_callback_aliases(self):
        update = callback_update()
        update["callback"]["message"] = update.pop("message")
        update["callback"]["data"] = update["callback"].pop("payload")
        update["callback"]["id"] = update["callback"].pop("callback_id")
        event = self.event_data(update)
        self.assertEqual(event["payload"], "TEST::CONFIRM::record-1")
        self.assertEqual(event["callback_id"], "callback-test")
        self.assertEqual(event["message_id"], "mid.test")

    def test_incoming_message_keeps_sender_and_command_fields(self):
        update = {"update_type": "message_created", "message": {
            "sender": {"user_id": 1001, "name": "Test actor"},
            "recipient": {"user_id": 9000, "chat_id": 3003},
            "body": {"mid": "mid.command", "text": "/Status@TestBot room 1"},
        }}
        event = self.event_data(update)
        self.assertEqual(event["type"], "message")
        self.assertEqual(event["user_id"], 1001)
        self.assertEqual(event["name"], "Test actor")
        self.assertEqual(event["command"], "status")
        self.assertEqual(event["args"], "room 1")

    def test_bot_started_uses_top_level_user(self):
        event = self.event_data({"update_type": "bot_started", "user": {"user_id": 1001},
                                 "chat_id": 3003, "payload": "start-test"})
        self.assertEqual(event["type"], "bot_started")
        self.assertEqual(event["payload"], "start-test")
        self.assertEqual(event["chat_id"], 3003)


if __name__ == "__main__":
    unittest.main()
