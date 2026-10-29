"""Independent retry journal for write-only disaster copies of failed orders."""

import asyncio
import json
import logging
import time
import uuid
from contextlib import suppress

from services.tilda_sheets import SheetsBackupError, backup_document

LOGGER = logging.getLogger("tilda.outbox")


class TildaBackupOutbox:
    def __init__(self, outbox, send=None):
        self.outbox, self.send = outbox, send
        self._wake = asyncio.Event()
        self._worker = None
        self._stopping = False

    @property
    def enabled(self):
        return self.send is not None

    def initialize(self):
        with self.outbox._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS webhook_sheet_backups (
                    id TEXT PRIMARY KEY, document TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    token TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
                    retry_at REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', completed_at REAL NOT NULL DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS webhook_sheet_backups_due ON webhook_sheet_backups(status,retry_at,lease_until)")

    def schedule(self, item_id, error):
        if not self.enabled:
            return
        with self.outbox._connect() as conn:
            row = conn.execute("SELECT * FROM webhook_outbox WHERE id=?", (item_id,)).fetchone()
            if not row or not row["submission"]:
                return
            document = backup_document(row["target"], json.loads(row["submission"]), json.loads(row["state"]), error)
            conn.execute("INSERT OR IGNORE INTO webhook_sheet_backups(id,document) VALUES (?,?)",
                         (item_id, json.dumps(document, ensure_ascii=False)))

    def status(self, item_id):
        with self.outbox._connect() as conn:
            row = conn.execute("SELECT status,url,last_error FROM webhook_sheet_backups WHERE id=?", (item_id,)).fetchone()
            if row:
                return {"status": "saved" if row["status"] == "done" else row["status"],
                        "url": row["url"], "error": row["last_error"]}
        return {"status": "disabled" if not self.enabled else "pending"}

    def claim(self, item_id):
        now, token = time.time(), uuid.uuid4().hex
        with self.outbox._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            updated = conn.execute("""
                UPDATE webhook_sheet_backups SET status='processing',token=?,lease_until=?,attempts=attempts+1
                WHERE id=? AND status<>'done' AND retry_at<=? AND lease_until<=?
            """, (token, now + self.outbox.lease_seconds, item_id, now, now)).rowcount
            row = conn.execute("SELECT * FROM webhook_sheet_backups WHERE id=?", (item_id,)).fetchone() if updated else None
            return dict(row) if row else None

    def renew(self, item):
        with self.outbox._connect() as conn:
            updated = conn.execute("UPDATE webhook_sheet_backups SET lease_until=? WHERE id=? AND token=?",
                                   (time.time() + self.outbox.lease_seconds, item["id"], item["token"])).rowcount
            if not updated:
                raise RuntimeError("backup_lease_lost")

    def finish(self, item, result=None, error=""):
        with self.outbox._connect() as conn:
            updated = conn.execute("""
                UPDATE webhook_sheet_backups SET status=?,document=CASE WHEN ? THEN '' ELSE document END,
                    token='',lease_until=0,retry_at=?,last_error=?,url=?,completed_at=?
                WHERE id=? AND token=?
            """, ("done" if result else "pending", bool(result), 0 if result else time.time() + self.outbox.retry_seconds,
                  error, result.get("url", "") if result else "", time.time() if result else 0, item["id"], item["token"])).rowcount
            if not updated:
                raise RuntimeError("backup_lease_lost")

    async def heartbeat(self, item):
        while True:
            await asyncio.sleep(self.outbox.lease_seconds / 3)
            await asyncio.to_thread(self.renew, item)

    async def process(self, item_id):
        if not self.enabled:
            return await asyncio.to_thread(self.status, item_id)
        item = await asyncio.to_thread(self.claim, item_id)
        if item:
            heartbeat = asyncio.create_task(self.heartbeat(item))
            try:
                result = await self.send(json.loads(item["document"]))
                if result.get("status") != "saved":
                    raise SheetsBackupError("sheets_unconfirmed")
                await asyncio.to_thread(self.finish, item, result)
                LOGGER.info("event=google_backup_saved submission_id=%s attempts=%s", item_id, item["attempts"])
            except asyncio.CancelledError:
                # Keep the lease: the HTTP thread may still finish a Google append.
                raise
            except Exception as exc:
                code = exc.code if isinstance(exc, SheetsBackupError) else "sheets_backup_failed"
                await asyncio.to_thread(self.finish, item, None, code)
                LOGGER.error("event=google_backup_pending submission_id=%s error=%s", item_id, code)
            finally:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat
        return await asyncio.to_thread(self.status, item_id)

    async def ensure(self, item_id, error):
        await asyncio.to_thread(self.schedule, item_id, error)
        self._wake.set()
        return await self.process(item_id)

    def due(self):
        with self.outbox._connect() as conn:
            return [row[0] for row in conn.execute("""
                SELECT id FROM webhook_sheet_backups WHERE status<>'done' AND retry_at<=? AND lease_until<=? LIMIT 50
            """, (time.time(), time.time()))]

    async def run_once(self):
        if self.enabled:
            for item_id in await asyncio.to_thread(self.due):
                await self.process(item_id)

    async def loop(self):
        while not self._stopping:
            self._wake.clear()
            try:
                await self.run_once()
            except Exception:
                LOGGER.error("event=google_backup_worker_waiting error=local_storage_unavailable")
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.outbox.retry_seconds)

    def start(self):
        if self.enabled:
            self._stopping = False
            self._worker = asyncio.create_task(self.loop(), name="tilda-sheets-backup")
            LOGGER.info("event=google_backup_queue_enabled")
        else:
            LOGGER.warning("event=google_backup_disabled reason=configuration_required")

    async def stop(self):
        self._stopping = True
        self._wake.set()
        if self._worker:
            try:
                await asyncio.wait_for(asyncio.shield(self._worker), timeout=5)
            except asyncio.TimeoutError:
                self._worker.cancel()
                with suppress(asyncio.CancelledError):
                    await self._worker
