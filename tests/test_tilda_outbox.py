"""Durability, recovery and side-effect safety; no external SMTP/CDEK calls."""

import asyncio
import json
import os
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import psycopg
import requests
from psycopg_pool import PoolTimeout

from tests.test_tilda_webhooks import MemoryOrders, shop, taplink_order
from services.tilda_outbox import TildaOutbox


class OutboxTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "private" / "outbox.sqlite3"
        self.orders = MemoryOrders()
        self.smtp = Mock()
        self.remote = Mock(return_value="request-id")
        self.find = Mock(return_value={"uuid": "cdek-uuid"})
        for name, value in {
            "save_tilda_submission": self.orders.save, "save_tilda_email_message": self.orders.email,
            "update_tilda_submission_im_number": self.orders.update, "send_customer_email": self.smtp,
            "cdek_get_token": Mock(return_value="test-token"), "find_cdek_order": self.find,
            "update_cdek_order_number": self.remote, "_cdek_account_or_404": Mock(return_value="test"),
            "_cdek_im_number_prefix": Mock(return_value="shop_"),
        }.items():
            self.stack.enter_context(patch.object(shop, name, value))
        self.box = self.new_box()
        await self.box.initialize()
        self.addAsyncCleanup(self.box.stop)

    def new_box(self, **kwargs):
        return TildaOutbox(self.path, shop._deliver_queued_submission, **kwargs)

    def record(self, record_id="submission", **overrides):
        return {"id": record_id, "site": "shelwear", "created_at": "2026-10-23T12:00:00+00:00",
                "payload_type": "json", "payload": taplink_order(), "cookies": {}, "client": {}, "headers": {}, **overrides}

    async def accept(self, record=None, target="test-db"):
        return await self.box.enqueue(target, record or self.record())

    def due_now(self, box=None):
        box = box or self.box
        box.blocked_until.clear()
        with box._connect() as conn:
            conn.execute("UPDATE webhook_outbox SET retry_at=0, lease_until=0 WHERE status <> 'done'")

    async def test_outage_is_durable_private_and_recovers_after_a_new_process(self):
        raw = self.record()
        with patch.object(shop, "save_tilda_submission", new_callable=AsyncMock,
                          side_effect=PoolTimeout("postgresql://user:password@private-host/db")):
            item_id, created = await self.accept(raw)
            self.assertTrue(created)
            with self.assertLogs("tilda.outbox", level="ERROR") as logs:
                response = await self.box.process(item_id)
            self.assertTrue(response["ok"])
            self.assertTrue(response["queued"])
            self.assertIn("PoolTimeout", " ".join(logs.output))
            self.assertNotIn("password", " ".join(logs.output))
            self.assertNotIn(raw["payload"]["email"], " ".join(logs.output))
            self.smtp.assert_not_called()
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        restarted = self.new_box()
        await restarted.initialize()
        pending = await restarted.pending_submissions("test-db", "shelwear")
        self.assertEqual(pending[0]["payload"], raw["payload"])
        self.due_now(restarted)
        await restarted.run_once()
        self.assertEqual(await restarted.pending_submissions("test-db", "shelwear"), [])
        self.assertEqual(self.orders.rows[item_id]["payload"], raw["payload"])
        self.smtp.assert_called_once()
        receipt = restarted._row(item_id)
        self.assertEqual(receipt["submission"], "")
        self.assertNotIn("buyer@example.test", receipt["result"])

    async def test_concurrent_retries_and_workers_do_not_duplicate_an_order_or_email(self):
        accepted = await asyncio.gather(*(self.accept(self.record(str(index))) for index in range(12)))
        self.assertEqual(len({row[0] for row in accepted}), 1)
        self.assertEqual(sum(row[1] for row in accepted), 1)
        self.box.lease_seconds = 0.09
        another = self.new_box(lease_seconds=0.09)

        def slow_smtp(*args):
            time.sleep(0.3)

        self.smtp.side_effect = slow_smtp
        first = asyncio.create_task(self.box.process(accepted[0][0]))
        await asyncio.sleep(0.15)
        second = await another.process(accepted[0][0])
        self.assertTrue(second["queued"])
        await first
        self.smtp.assert_called_once()
        self.assertEqual(len(self.orders.rows), 1)
        again = await another.process(accepted[0][0])
        self.assertFalse(again["queued"])

    async def test_commit_with_a_lost_response_resumes_owned_order(self):
        first_attempt = True

        async def ambiguous_save(target, record):
            nonlocal first_attempt
            result = await self.orders.save(target, record)
            if first_attempt:
                first_attempt = False
                raise psycopg.OperationalError("connection lost after commit")
            return result

        with patch.object(shop, "save_tilda_submission", side_effect=ambiguous_save):
            item_id, _ = await self.accept()
            self.assertTrue((await self.box.process(item_id))["queued"])
            self.due_now()
            self.assertFalse((await self.box.process(item_id))["queued"])
        self.assertEqual(len(self.orders.rows), 1)
        self.smtp.assert_called_once()

    async def test_existing_postgresql_order_is_not_notified_again(self):
        await self.orders.save("test-db", self.record("existing-id"))
        item_id, _ = await self.accept()
        response = await self.box.process(item_id)
        self.assertTrue(response["duplicate"])
        self.assertEqual(response["submission_id"], "existing-id")
        self.smtp.assert_not_called()

    async def test_email_history_failure_retries_history_without_resending_smtp(self):
        save_history = AsyncMock(side_effect=PoolTimeout("database unavailable"))
        item_id, _ = await self.accept()
        with patch.object(shop, "save_tilda_email_message", save_history):
            self.assertTrue((await self.box.process(item_id))["queued"])
        self.assertEqual(json.loads(self.box._row(item_id)["state"])["email"]["status"], "sent")
        restarted = self.new_box()
        self.due_now(restarted)
        await restarted.run_once()
        self.smtp.assert_called_once()
        self.assertEqual(len(self.orders.rows[item_id]["email_messages"]), 1)
        self.assertEqual(await restarted.pending_submissions("test-db", "shelwear"), [])

    async def test_cdek_result_database_failure_does_not_repeat_the_remote_update(self):
        raw = self.record()
        raw["payload"]["shipping_fields"] = {"shipping_method": "Доставка СДЭК"}
        item_id, _ = await self.accept(raw)
        with patch.object(shop, "update_tilda_submission_im_number", new_callable=AsyncMock,
                          side_effect=psycopg.OperationalError("connection closed")):
            self.assertTrue((await self.box.process(item_id))["queued"])
        restarted = self.new_box()
        self.due_now(restarted)
        await restarted.run_once()
        self.remote.assert_called_once()
        self.find.assert_called_once()
        self.smtp.assert_called_once()
        self.assertEqual(self.orders.rows[item_id]["im_number"], "shop_27")

    async def test_unknown_smtp_outcome_after_restart_is_not_resent(self):
        item_id, _ = await self.accept()
        await self.orders.save("test-db", self.record())
        item = self.box._claim(item_id)
        item.state.update(database_id=item_id, owned=True, email={"status": "sending"})
        await item.checkpoint()
        self.box._retry(item, "shutdown")
        self.due_now()
        with self.assertLogs("tilda.webhook", level="ERROR"):
            result = await self.box.process(item_id)
        self.assertEqual(result["auto_actions"]["email_status"], "unknown")
        self.smtp.assert_not_called()
        self.assertIn(item_id, self.orders.rows)

    async def test_cdek_forbidden_is_reported_without_losing_or_requeueing_the_order(self):
        raw = self.record()
        raw["payload"]["shipping_fields"] = {"shipping_method": "Доставка СДЭК"}
        response = requests.Response()
        response.status_code = 403
        response.reason = "Forbidden"
        self.remote.side_effect = requests.HTTPError(response=response)
        item_id, _ = await self.accept(raw)
        result = await self.box.process(item_id)
        self.assertFalse(result["queued"])
        self.assertEqual(result["auto_actions"]["im_number_error"], "HTTP 403 Forbidden")
        self.assertIn(item_id, self.orders.rows)
        self.assertEqual(await self.box.pending_submissions("test-db", "shelwear"), [])
        self.smtp.assert_called_once()

    async def test_worker_retries_automatically_without_a_new_webhook(self):
        available = False
        attempts = 0

        async def prepare(target):
            nonlocal attempts
            attempts += 1
            if not available:
                raise PoolTimeout("database unavailable")

        self.box.prepare = prepare
        self.assertEqual(self.box.retry_seconds, 60)
        self.box.retry_seconds = 0.05
        await self.accept()
        await self.box.start(["test-db"])
        await asyncio.sleep(0.07)
        available = True

        async def wait_for_delivery():
            while await self.box.pending_submissions("test-db", "shelwear"):
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_for_delivery(), timeout=2)
        self.assertGreaterEqual(attempts, 2)
        self.smtp.assert_called_once()

    async def test_a_failed_database_does_not_block_another_project(self):
        async def prepare(target):
            if target == "offline-db":
                raise PoolTimeout("offline")

        self.box.prepare = prepare
        await self.accept(self.record("offline"), target="offline-db")
        await self.accept(self.record("online"), target="online-db")
        await self.box.run_once()
        self.assertEqual(len(await self.box.pending_submissions("offline-db", "shelwear")), 1)
        self.assertEqual(await self.box.pending_submissions("online-db", "shelwear"), [])
        self.smtp.assert_called_once()


class StartupTests(unittest.TestCase):
    def test_full_app_can_start_and_accept_orders_with_postgresql_offline(self):
        from fastapi.testclient import TestClient
        from db import connections
        from main import app

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            targets = {name: connections.DatabaseTarget(name, "unused_test_database") for name in (
                "apex", "real_baby", "ama_living", "wayfs", "feralru", "shelwear",
            )}
            stack.enter_context(patch.object(connections, "DATABASE_TARGETS", targets))
            stack.enter_context(patch.object(shop.config, "POSTGRESQL_HOST", "127.0.0.1:9"))
            stack.enter_context(patch.object(shop.config, "POSTGRESQL_POOL_TIMEOUT_SECONDS", 0.05))
            stack.enter_context(patch.object(shop.config, "TILDA_PROJECTS_JSON", json.dumps({
                "shelwear": {"secret": "test-only", "database_target": "shelwear", "cdek_account": "test"},
            })))
            box = TildaOutbox(Path(directory) / "outbox.sqlite3", shop._deliver_queued_submission,
                              prepare=shop.create_tilda_submissions_schema)
            stack.enter_context(patch.object(shop, "_get_tilda_outbox", return_value=box))
            smtp = stack.enter_context(patch.object(shop, "send_customer_email"))
            with TestClient(app) as client:
                self.assertEqual(client.get("/tilda/shelwear/form?secret=test-only").status_code, 200)
                response = client.post("/tilda/shelwear/webhook", json=taplink_order())
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["queued"])
                state = client.get("/tilda/shelwear/form/submissions?secret=test-only").json()
                self.assertEqual(state["pending_count"], 1)
            smtp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
