"""Disaster copy reliability. Google HTTP, SMTP and CDEK are always mocked."""

import asyncio
import json
import io
import sys
import tempfile
import time
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from psycopg_pool import PoolTimeout

from tests.test_tilda_webhooks import taplink_order
from services.tilda_outbox import TildaOutbox
from services.tilda_sheets import GoogleSheetsBackup, SheetsBackupError, backup_document, backup_rows


def submission():
    return {"id": "original-id", "site": "shelwear", "created_at": "2026-10-29T12:00:00+00:00", "payload_type": "json",
            "payload": taplink_order(), "cookies": {"source": "test"}, "client": {"host": "192.0.2.1", "port": 1000},
            "headers": {"content-type": "application/json"}, "im_number": "=formula-must-be-text"}


class SheetsClientTests(unittest.TestCase):
    def test_all_database_fields_and_full_json_are_written_as_raw_cells_without_reads(self):
        document = backup_document("test-db", submission(), {}, "PoolTimeout")
        client = GoogleSheetsBackup("test_spreadsheet_id", "not-real-credentials", "Заказы ' магазинов")
        context = MagicMock()
        session = context.__enter__.return_value
        session.request.return_value = Mock(ok=True, json=Mock(return_value={"updates": {"updatedRows": 1}}))
        with patch.object(client, "session", return_value=context):
            result = client.send(document)
        self.assertEqual(result["status"], "saved")
        self.assertEqual([call.args[0] for call in session.request.call_args_list], ["PUT", "POST"])
        request = session.request.call_args
        self.assertEqual(request.kwargs["params"]["valueInputOption"], "RAW")
        row, = request.kwargs["json"]["values"]
        self.assertEqual(row[7], "=formula-must-be-text")
        restored = json.loads(row[12])
        self.assertEqual(restored["payload"], submission()["payload"])
        self.assertEqual(restored["cookies"], submission()["cookies"])
        self.assertEqual(restored["headers"], submission()["headers"])
        self.assertEqual(restored["webhook_key"], "order:27956212")
        self.assertNotIn("credentials", row[12])

    def test_large_unicode_json_is_chunked_without_losing_data(self):
        raw = submission()
        raw["payload"]["large"] = "🏐" * 35000 + "тест" * 20000
        document = backup_document("test-db", raw, {"email": {"status": "sending"}}, "PoolTimeout")
        rows = backup_rows(document)
        self.assertGreater(len(rows), 1)
        for row in rows:
            self.assertTrue(all(len(str(cell).encode("utf-16-le")) // 2 < 50000 for cell in row))
        restored = json.loads("".join(row[12] for row in rows))
        self.assertEqual(restored["payload"], raw["payload"])
        self.assertEqual(json.loads("".join(row[17] for row in rows)), document["queue_state"])

    def test_provider_errors_are_safe_and_do_not_contain_payloads_or_credentials(self):
        session = Mock()
        session.request.return_value = Mock(ok=False, status_code=403, text="private-key buyer@example.test")
        with self.assertRaises(SheetsBackupError) as raised:
            GoogleSheetsBackup.request(session, "POST", "https://sheets.googleapis.com/test")
        self.assertEqual(str(raised.exception), "sheets_permission_or_quota_denied")

    def test_create_command_returns_actual_provider_id_and_shares_only_with_requested_owner(self):
        from scripts.tilda_sheets_admin import main
        client = GoogleSheetsBackup("", "test-only")
        context = MagicMock()
        with patch.object(client, "session", return_value=context), \
                patch.object(client, "request", side_effect=[{"spreadsheetId": "created_spreadsheet_id"}, {}]) as request, \
                patch.object(client, "initialize") as initialize, \
                patch("routers.tilda_apex_webhook._sheets_backup_client", return_value=client), \
                patch.object(sys, "argv", ["tilda_sheets_admin", "create", "--share-with", "owner@example.test"]), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["spreadsheet_url"], "https://docs.google.com/spreadsheets/d/created_spreadsheet_id/edit")
        self.assertEqual(request.call_args.kwargs["json"]["emailAddress"], "owner@example.test")
        self.assertEqual(request.call_args.kwargs["params"]["sendNotificationEmail"], "false")
        initialize.assert_called_once()

    def test_invalid_credentials_never_expose_their_value(self):
        client = GoogleSheetsBackup("test_spreadsheet_id", "private-invalid-json")
        with self.assertRaises(SheetsBackupError) as raised:
            client.session()
        self.assertEqual(str(raised.exception), "sheets_credentials_invalid")


class BackupQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "queue.sqlite3"
        self.deliver = AsyncMock(side_effect=PoolTimeout("private DSN"))
        self.google = AsyncMock(return_value={"status": "saved", "url": "https://docs.google.com/spreadsheets/d/test/edit"})
        self.box = self.new_box()
        await self.box.initialize()
        self.addAsyncCleanup(self.box.stop)
        self.item_id, _ = await self.box.enqueue("test-db", submission())

    def new_box(self):
        return TildaOutbox(self.path, self.deliver, backup=self.google)

    async def test_sqlite_then_google_then_eventual_postgresql_without_duplicate_copy(self):
        result = await self.box.process(self.item_id)
        self.assertTrue(result["queued"])
        self.assertEqual(result["external_backup"]["status"], "saved")
        self.assertEqual(self.google.call_args.args[0]["submission"]["payload"], submission()["payload"])
        await self.box.process(self.item_id)
        self.google.assert_awaited_once()

        self.deliver.side_effect = None
        self.deliver.return_value = {"ok": True, "storage": "database"}
        self.box.blocked_until.clear()
        with self.box._connect() as conn:
            conn.execute("UPDATE webhook_outbox SET retry_at=0")
        await self.box.process(self.item_id)
        self.assertEqual(self.box._row(self.item_id)["status"], "done")
        self.google.assert_awaited_once()

    async def test_healthy_database_does_not_copy_order_to_google(self):
        self.deliver.side_effect = None
        self.deliver.return_value = {"ok": True, "storage": "database"}
        result = await self.box.process(self.item_id)
        self.assertEqual(result["storage"], "database")
        self.google.assert_not_awaited()

    async def test_backup_survives_restart_and_database_recovery_does_not_drop_pending_copy(self):
        self.google.side_effect = SheetsBackupError("sheets_connection_error")
        with self.assertLogs("tilda.outbox", level="ERROR"):
            result = await self.box.process(self.item_id)
        self.assertEqual(result["external_backup"]["status"], "pending")
        self.deliver.side_effect = None
        self.deliver.return_value = {"ok": True, "storage": "database"}
        self.box.blocked_until.clear()
        with self.box._connect() as conn:
            conn.execute("UPDATE webhook_outbox SET retry_at=0")
        await self.box.process(self.item_id)
        self.assertEqual(self.box._row(self.item_id)["submission"], "")
        with self.box._connect() as conn:
            conn.execute("UPDATE webhook_outbox SET completed_at=?", (time.time() - 2 * 86400,))
        self.box._cleanup()
        self.assertIsNotNone(self.box._row(self.item_id))
        restarted = self.new_box()
        await restarted.initialize()
        self.google.side_effect = None
        with restarted._connect() as conn:
            conn.execute("UPDATE webhook_sheet_backups SET retry_at=0")
        await restarted.backup.run_once()
        self.assertEqual(restarted.backup.status(self.item_id)["status"], "saved")
        restored = self.google.call_args.args[0]["submission"]
        self.assertEqual(restored["payload"], submission()["payload"])
        with restarted._connect() as conn:
            self.assertEqual(conn.execute("SELECT document FROM webhook_sheet_backups").fetchone()[0], "")

    async def test_parallel_workers_append_one_copy(self):
        another = self.new_box()
        await another.initialize()
        await asyncio.gather(self.box.backup.ensure(self.item_id, "PoolTimeout"), another.backup.ensure(self.item_id, "PoolTimeout"))
        self.google.assert_awaited_once()
        self.assertEqual(self.box.backup.status(self.item_id)["status"], "saved")

    async def test_backup_worker_runs_even_when_schema_is_unavailable(self):
        self.box.prepare = AsyncMock(side_effect=PoolTimeout("offline"))
        self.box.retry_seconds = 0.05
        await self.box.start(["test-db"])
        async with asyncio.timeout(2):
            while self.box.backup.status(self.item_id)["status"] != "saved":
                await asyncio.sleep(0.01)
        self.google.assert_awaited_once()
        self.assertNotEqual(self.box._row(self.item_id)["status"], "done")


if __name__ == "__main__":
    unittest.main()
