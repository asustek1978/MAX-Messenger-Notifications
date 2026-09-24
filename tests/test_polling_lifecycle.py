"""Offline startup/unload tests with an indefinitely pending MAX request.

HA's normal/background task tracking is modeled separately. These tests run
the integration's real setup, polling and unload functions, without network.
"""

from __future__ import annotations

import ast
import asyncio
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_message_tracking import COMPONENT, SOURCE, FakeStore
import runpy


class PendingApi:
    def __init__(self, fail=False):
        self.entered = asyncio.Event()
        self.cancelled = False
        self.fail = fail

    async def get_updates(self, marker, timeout):
        self.entered.set()
        if self.fail:
            raise RuntimeError("simulated MAX outage")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class FakeHass:
    def __init__(self, api):
        self.api = api
        self.data = {}
        self.disk = {}
        self.tasks = set()
        self.background_tasks = set()

    def _create(self, coro, name, bucket):
        task = asyncio.create_task(coro, name=name)
        bucket.add(task)
        task.add_done_callback(bucket.discard)
        return task

    def async_create_task(self, coro, name):
        return self._create(coro, name, self.tasks)

    def async_create_background_task(self, coro, name, eager_start=True):
        return self._create(coro, name, self.background_tasks)

    async def startup_barrier(self):
        # Like HA's default startup barrier, exclude lifetime background work.
        while pending := [task for task in self.tasks if not task.done()]:
            await asyncio.wait(pending)


class FakeEntry:
    def __init__(self, polling=True):
        self.entry_id = "test-entry"
        self.data = {"token": "test-only", "polling": polling}
        self.options = {}
        self.background_tasks = set()
        self.async_on_unload = Mock()
        self.add_update_listener = Mock()

    def async_create_background_task(self, hass, coro, name, eager_start=True):
        task = hass.async_create_background_task(coro, name, eager_start)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        return task


def load_functions():
    names = {"async_setup_entry", "async_unload_entry", "_poll_loop", "_conf", "_to_bool"}
    tree = ast.parse(SOURCE.read_text())
    body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    body.extend(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names)
    ns = runpy.run_path(str(COMPONENT / "const.py"))
    ns.update(asyncio=asyncio, Store=FakeStore, SlavaMaxApi=lambda session, token: session,
              async_get_clientsession=lambda hass: hass.api, _async_update_listener=Mock(),
              _LOGGER=logging.getLogger("max_poll_test"))
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(SOURCE), "exec"), ns)
    return ns


class PollingLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ns = load_functions()
        self.api = PendingApi()
        self.hass = FakeHass(self.api)
        self.entry = FakeEntry()

    async def asyncTearDown(self):
        await self.ns["async_unload_entry"](self.hass, self.entry)

    async def start(self):
        self.assertTrue(await self.ns["async_setup_entry"](self.hass, self.entry))
        await asyncio.wait_for(self.api.entered.wait(), 1)
        return self.hass.data["slava_max"][self.entry.entry_id]

    async def test_startup_finishes_while_request_is_pending(self):
        runtime = await self.start()
        await asyncio.wait_for(self.hass.startup_barrier(), 0.1)
        self.assertFalse(runtime["task"].done())
        self.assertIn(runtime["task"], self.entry.background_tasks)

    async def test_unload_cancels_pending_request_and_reload_has_one_poller(self):
        runtime = await self.start()
        first_task = runtime["task"]
        await self.ns["async_unload_entry"](self.hass, self.entry)
        self.assertTrue(runtime["stop"].is_set())
        self.assertTrue(first_task.cancelled())
        self.assertTrue(self.api.cancelled)
        self.assertNotIn(self.entry.entry_id, self.hass.data["slava_max"])
        self.api = self.hass.api = PendingApi()
        second = await self.start()
        self.assertIsNot(second["task"], first_task)
        self.assertEqual(len([t for t in self.entry.background_tasks if not t.done()]), 1)

    async def test_api_outage_retry_does_not_hold_startup_or_unload(self):
        self.api.fail = True
        with self.assertLogs("max_poll_test", level="WARNING"):
            runtime = await self.start()
        await asyncio.wait_for(self.hass.startup_barrier(), 0.1)
        self.assertFalse(runtime["task"].done())
        await asyncio.wait_for(self.ns["async_unload_entry"](self.hass, self.entry), 0.1)
        self.assertTrue(runtime["task"].cancelled())

    async def test_polling_disabled_creates_no_task(self):
        self.entry.data["polling"] = False
        self.assertTrue(await self.ns["async_setup_entry"](self.hass, self.entry))
        runtime = self.hass.data["slava_max"][self.entry.entry_id]
        self.assertIsNone(runtime["task"])
        self.assertIsNone(runtime["stop"])
        self.assertFalse(self.hass.tasks | self.hass.background_tasks)


if __name__ == "__main__":
    unittest.main()
