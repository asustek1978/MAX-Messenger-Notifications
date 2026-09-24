"""Offline regression tests; HA storage/session boundaries use test doubles.

Run: python -m unittest discover -s tests -v
No Home Assistant installation, bot token, or network connection is required.
Functions are compiled from the integration source to isolate HA-only imports.
These tests do not replace a real Home Assistant startup/delivery test.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import logging
import os
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "slava_max"
SOURCE = Path(os.environ.get("MAX_TEST_SOURCE", COMPONENT / "__init__.py"))


class FakeStore:
    def __init__(self, hass, version, key):
        self.hass = hass
        self.key = key
        self.version = version
        self.fail_save = False

    async def async_load(self):
        return copy.deepcopy(self.hass.disk.get(self.key))

    async def async_save(self, data):
        if self.fail_save:
            raise OSError("test disk error")
        self.hass.disk[self.key] = copy.deepcopy(data)


class FakeApi:
    def __init__(self):
        self.events = []
        self.next_id = 0
        self.fail_edit = False
        self.fail_send = False
        self.missing_mid = False

    async def send_message(self, **kwargs):
        await asyncio.sleep(0)  # Allow concurrent service calls to interleave.
        if self.fail_send:
            raise RuntimeError("test send failure")
        self.next_id += 1
        mid = f"mid.new-{self.next_id}"
        self.events.append(("send", mid, kwargs))
        return {"message": {"body": {"text": kwargs["text"], **({} if self.missing_mid else {"mid": mid})}}}

    async def edit_message(self, **kwargs):
        if self.fail_edit:
            raise RuntimeError("test edit failure")
        self.events.append(("edit", kwargs["message_id"], kwargs))
        return {"success": True}

    async def delete_message(self, **kwargs):
        self.events.append(("delete", kwargs["message_id"], kwargs))
        return {"success": True}


def load_functions():
    names = {
        "_extract_message_id", "_message_store", "_message_runtime",
        "_message_store_key", "_remember_message_id", "_send_or_update",
        "_send_or_replace", "_api", "_send_message", "_conf", "_to_bool",
        "async_setup_entry", "_broadcast_or_update_message",
    }
    tree = ast.parse(SOURCE.read_text())
    body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    body.extend(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names)
    module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
    namespace = runpy.run_path(str(COMPONENT / "const.py"))
    namespace.update(
        asyncio=asyncio, Store=FakeStore, HomeAssistantError=RuntimeError,
        SlavaMaxApiError=RuntimeError,
        SlavaMaxApi=lambda session, token: session,
        async_get_clientsession=lambda hass: hass.api,
        _async_update_listener=Mock(), _fallback_notify=AsyncMock(),
        _LOGGER=logging.getLogger("max_tracking_test"),
        _is_allowed=lambda *args: True,
    )
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace


class TrackingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ns = load_functions()
        self.hass, self.entry = await self.start()

    async def start(self, disk=None, entry_id="entry-a", api=None):
        hass = SimpleNamespace(data={}, disk=disk if disk is not None else {}, api=api or FakeApi())
        entry = SimpleNamespace(entry_id=entry_id, data={"token": "test-only", "polling": False}, options={},
                                async_on_unload=Mock(), add_update_listener=Mock())
        await self.ns["async_setup_entry"](hass, entry)
        return hass, entry

    async def call(self, kind="_send_or_update", *, hass=None, entry=None, **overrides):
        params = dict(key="status", message="first", target_type="chat_id", target_id=101,
                      fmt="markdown", notify=True, buttons=None, disable_link_preview=False, emergency=False)
        params.update(overrides)
        return await self.ns[kind](hass or self.hass, entry or self.entry, **params)

    def test_documented_response_and_compatible_shapes(self):
        extract = self.ns["_extract_message_id"]
        self.assertEqual(extract({"message": {"body": {"mid": "mid.documented"}}}), "mid.documented")
        for response in ({"mid": "m"}, {"message_id": "m"}, {"body": {"mid": "m"}},
                         {"message": {"mid": "m"}}, {"message": {"body": {"message_id": "m"}}}):
            with self.subTest(response=response):
                self.assertEqual(extract(response), "m")
        for response in (None, {}, {"message": None}, {"mid": []}, {"mid": True}, {"mid": " "}):
            self.assertIsNone(extract(response))

    async def test_second_call_edits_same_message(self):
        await self.call()
        await self.call(message="second", notify=False, buttons=[])
        events = self.hass.api.events
        self.assertEqual([e[0] for e in events], ["send", "edit"])
        self.assertEqual(events[0][1], events[1][1])
        self.assertEqual(events[1][2]["text"], "second")
        self.assertFalse(events[1][2]["notify"])
        self.assertEqual(events[1][2]["buttons"], [])

    async def test_restart_restores_message_id(self):
        await self.call()
        restarted, entry = await self.start(disk=self.hass.disk)
        await self.call(hass=restarted, entry=entry, message="after restart")
        self.assertEqual([(e[0], e[1]) for e in restarted.api.events], [("edit", "mid.new-1")])

    async def test_loads_v070_storage_format(self):
        disk = {"slava_max.entry-a.message_keys": {"regular:chat_id:101:status": "mid.legacy"}}
        hass, entry = await self.start(disk=disk)
        await self.call(hass=hass, entry=entry)
        self.assertEqual([(e[0], e[1]) for e in hass.api.events], [("edit", "mid.legacy")])

    async def test_parallel_calls_send_once(self):
        await asyncio.gather(*(self.call(message=str(n)) for n in range(5)))
        self.assertEqual([e[0] for e in self.hass.api.events], ["send", "edit", "edit", "edit", "edit"])

    async def test_separate_keys_targets_scopes_and_entries(self):
        for params in ({}, {"key": "other"}, {"target_id": 202}, {"target_type": "user_id"}, {"emergency": True}):
            await self.call(**params)
        for params in ({}, {"key": "other"}, {"target_id": 202}, {"target_type": "user_id"}, {"emergency": True}):
            await self.call(**params)
        self.assertEqual([e[0] for e in self.hass.api.events], ["send"] * 5 + ["edit"] * 5)
        other, entry = await self.start(disk=self.hass.disk, entry_id="entry-b")
        await self.call(hass=other, entry=entry)
        self.assertEqual(other.api.events[0][0], "send")

    async def test_replacement_sends_then_deletes_and_persists(self):
        await self.call()
        await self.call(kind="_send_or_replace", message="replacement")
        self.assertEqual([(e[0], e[1]) for e in self.hass.api.events],
                         [("send", "mid.new-1"), ("send", "mid.new-2"), ("delete", "mid.new-1")])
        restarted, entry = await self.start(disk=self.hass.disk)
        await self.call(hass=restarted, entry=entry)
        self.assertEqual(restarted.api.events[0][1], "mid.new-2")

    async def test_edit_failure_rebinds_to_new_message(self):
        await self.call()
        self.hass.api.fail_edit = True
        with self.assertLogs("max_tracking_test", level="WARNING"):
            await self.call()
        self.hass.api.fail_edit = False
        await self.call()
        self.assertEqual([(e[0], e[1]) for e in self.hass.api.events],
                         [("send", "mid.new-1"), ("send", "mid.new-2"), ("edit", "mid.new-2")])

    async def test_failed_replacement_keeps_previous_message(self):
        await self.call()
        disk_before = copy.deepcopy(self.hass.disk)
        self.hass.api.fail_send = True
        with self.assertRaisesRegex(RuntimeError, "test send failure"):
            await self.call(kind="_send_or_replace")
        self.assertEqual(self.hass.disk, disk_before)
        self.assertEqual(len(self.hass.api.events), 1)

    async def test_missing_mid_warns_and_does_not_delete_previous(self):
        await self.call()
        self.hass.api.missing_mid = True
        with self.assertLogs("max_tracking_test", level="WARNING") as logs:
            await self.call(kind="_send_or_replace")
        self.assertIn("message.body.mid", " ".join(logs.output))
        self.assertEqual([e[0] for e in self.hass.api.events], ["send", "send"])
        await self.call()
        self.assertEqual(self.hass.api.events[-1][1], "mid.new-1")

    async def test_storage_error_keeps_in_memory_id(self):
        self.hass.data["slava_max"][self.entry.entry_id]["message_store"].fail_save = True
        with self.assertLogs("max_tracking_test", level="ERROR"):
            await self.call()
        await self.call()
        self.assertEqual([e[0] for e in self.hass.api.events], ["send", "edit"])

    async def test_broadcast_updates_each_recipient(self):
        params = dict(key="broadcast-status", message="first", user_ids=[1001, 1002], required_permission=None,
                      fmt="markdown", notify=True, buttons=None, disable_link_preview=False)
        await self.ns["_broadcast_or_update_message"](self.hass, self.entry, **params)
        params["message"] = "second"
        await self.ns["_broadcast_or_update_message"](self.hass, self.entry, **params)
        self.assertEqual([(e[0], e[1]) for e in self.hass.api.events],
                         [("send", "mid.new-1"), ("send", "mid.new-2"), ("edit", "mid.new-1"), ("edit", "mid.new-2")])

    async def test_empty_key_fails_before_sending(self):
        with self.assertRaisesRegex(RuntimeError, "key"):
            await self.call(key="  ")
        self.assertEqual(self.hass.api.events, [])


if __name__ == "__main__":
    unittest.main()
