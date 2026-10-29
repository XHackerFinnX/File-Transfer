"""Durable local inbox for shop webhooks, independent of PostgreSQL.

SQLite commits precede acknowledgement. Leases coordinate Uvicorn workers;
checkpoints let delivery resume without replaying completed external actions.
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from services.tilda_payload import order_number, webhook_key

LOGGER = logging.getLogger("tilda.outbox")
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    LOGGER.addHandler(handler)


class LeaseLost(RuntimeError):
    pass


@dataclass
class Delivery:
    id: str
    target: str
    submission: dict[str, Any]
    state: dict[str, Any]
    token: str
    attempts: int
    outbox: "TildaOutbox" = field(repr=False)

    async def checkpoint(self):
        await asyncio.to_thread(self.outbox._checkpoint, self)


class TildaOutbox:
    def __init__(self, path: str | Path, deliver: Callable[[Delivery], Awaitable[dict]],
                 prepare: Callable[[str], Awaitable[None]] | None = None,
                 retry_seconds: float = 60, lease_seconds: float = 120, batch_size: int = 50,
                 recover: Callable[[str], Awaitable[None]] | None = None):
        self.path = Path(path).resolve()
        self.deliver = deliver
        self.prepare = prepare
        self.recover = recover
        self.retry_seconds = retry_seconds
        self.lease_seconds = lease_seconds
        self.batch_size = batch_size
        self.targets: set[str] = set()
        self.ready: set[str] = set()
        self.blocked_until: dict[str, float] = {}
        self._prepare_locks: dict[str, asyncio.Lock] = {}
        self._recover_targets: set[str] = set()
        self.queue_id = ""
        self._wake = asyncio.Event()
        self._stopping = False
        self._worker: asyncio.Task | None = None
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA synchronous=FULL")
            with conn:
                yield conn
        finally:
            conn.close()

    def _initialize(self):
        existed = self.path.is_file()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS webhook_outbox (
                    id TEXT PRIMARY KEY, target TEXT NOT NULL, site TEXT NOT NULL,
                    webhook_key TEXT NOT NULL, submission TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'pending',
                    token TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
                    retry_at REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '{}',
                    completed_at REAL NOT NULL DEFAULT 0,
                    UNIQUE (target, site, webhook_key)
                );
                CREATE INDEX IF NOT EXISTS webhook_outbox_pending
                    ON webhook_outbox(status, retry_at);
                CREATE TABLE IF NOT EXISTS webhook_outbox_meta (
                    name TEXT PRIMARY KEY, value TEXT NOT NULL
                );
            """)
            conn.execute("INSERT OR IGNORE INTO webhook_outbox_meta(name,value) VALUES ('queue_id',?)", (uuid.uuid4().hex,))
            self.queue_id = conn.execute("SELECT value FROM webhook_outbox_meta WHERE name='queue_id'").fetchone()[0]
            pending = conn.execute("SELECT count(*) FROM webhook_outbox WHERE status<>'done'").fetchone()[0]
        self._log(logging.INFO if existed else logging.WARNING,
                  "Открыта существующая локальная очередь" if existed else
                  "Создан новый файл очереди: для сохранности при передеплое нужен постоянный диск")
        LOGGER.info("queue_id=%s pending_count=%s", self.queue_id, pending)

    async def initialize(self):
        async with self._initialize_lock:
            if not self._initialized:
                await asyncio.to_thread(self._initialize)
                self._initialized = True

    def _enqueue(self, target, submission):
        with self._connect() as conn:
            inserted = conn.execute("""
                INSERT INTO webhook_outbox(id, target, site, webhook_key, submission)
                VALUES (?, ?, ?, ?, ?) ON CONFLICT(target, site, webhook_key) DO NOTHING
            """, (submission["id"], target, submission["site"], webhook_key(submission["payload"]),
                  json.dumps(submission, ensure_ascii=False))).rowcount
            row = conn.execute("""
                SELECT id FROM webhook_outbox WHERE target=? AND site=? AND webhook_key=?
            """, (target, submission["site"], webhook_key(submission["payload"]))).fetchone()
            return str(row["id"]), bool(inserted)

    async def enqueue(self, target, submission):
        result = await asyncio.to_thread(self._enqueue, target, submission)
        self.targets.add(target)
        self._wake.set()
        return result

    def _row(self, item_id):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM webhook_outbox WHERE id=?", (item_id,)).fetchone()
            return dict(row) if row else None

    def _claim(self, item_id):
        now, token = time.time(), uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            updated = conn.execute("""
                UPDATE webhook_outbox SET status='processing', token=?, lease_until=?, attempts=attempts+1
                WHERE id=? AND status <> 'done' AND retry_at<=? AND lease_until<=?
            """, (token, now + self.lease_seconds, item_id, now, now)).rowcount
            if not updated:
                return None
            row = conn.execute("SELECT * FROM webhook_outbox WHERE id=?", (item_id,)).fetchone()
            return Delivery(row["id"], row["target"], json.loads(row["submission"]),
                            json.loads(row["state"]), token, row["attempts"], self)

    def _checkpoint(self, item):
        with self._connect() as conn:
            updated = conn.execute("""
                UPDATE webhook_outbox SET state=?, lease_until=? WHERE id=? AND token=?
            """, (json.dumps(item.state, ensure_ascii=False), time.time() + self.lease_seconds,
                  item.id, item.token)).rowcount
            if not updated:
                raise LeaseLost("Local delivery lease changed")

    def _finish(self, item, result):
        with self._connect() as conn:
            updated = conn.execute("""
                UPDATE webhook_outbox SET status='done', submission='', state='{}',
                    token='', lease_until=0, completed_at=?, result=?
                WHERE id=? AND token=?
            """, (time.time(), json.dumps(result, ensure_ascii=False), item.id, item.token)).rowcount
            if not updated:
                raise LeaseLost("Local delivery lease changed")

    def _retry(self, item, error):
        with self._connect() as conn:
            conn.execute("""
                UPDATE webhook_outbox SET status='pending', token='', lease_until=0,
                    retry_at=?, last_error=? WHERE id=? AND token=?
            """, (time.time() + self.retry_seconds, error, item.id, item.token))

    @staticmethod
    def _queued(row):
        return {"ok": True, "site": row["site"], "submission_id": row["id"],
                "queued": True, "storage": "local"}

    def _log(self, level, step, item=None, target="", error=""):
        data = {"шаг": step, "database_target": target, "retry_seconds": self.retry_seconds,
                "queue_path": str(self.path), "queue_id": self.queue_id}
        if item:
            data.update(site=item.submission["site"], submission_id=item.id,
                        order_id=order_number(item.submission["payload"]), attempts=item.attempts)
        if error:
            data["error_type"] = error
        LOGGER.log(level, json.dumps(data, ensure_ascii=False, separators=(",", ":")))

    async def _ensure_ready(self, target):
        lock = self._prepare_locks.setdefault(target, asyncio.Lock())
        async with lock:
            if target not in self.ready:
                if target in self._recover_targets and self.recover:
                    await self.recover(target)
                    self._recover_targets.discard(target)
                if self.prepare:
                    await self.prepare(target)
                self.ready.add(target)
                if target in self.blocked_until:
                    self.blocked_until.pop(target, None)
                    self._log(logging.INFO, "Связь с БД восстановлена, переносим локальную очередь", target=target)

    def database_unavailable(self, target):
        self.ready.discard(target)
        self._recover_targets.add(target)
        self.blocked_until[target] = time.time() + self.retry_seconds
        self._wake.set()

    async def _heartbeat(self, item):
        while True:
            await asyncio.sleep(self.lease_seconds / 3)
            await asyncio.to_thread(self._renew, item)

    def _renew(self, item):
        with self._connect() as conn:
            updated = conn.execute("UPDATE webhook_outbox SET lease_until=? WHERE id=? AND token=?",
                                   (time.time() + self.lease_seconds, item.id, item.token)).rowcount
            if not updated:
                raise LeaseLost("Local delivery lease changed")

    async def process(self, item_id):
        row = await asyncio.to_thread(self._row, item_id)
        if row["status"] == "done":
            return json.loads(row["result"])
        if self.blocked_until.get(row["target"], 0) > time.time():
            return self._queued(row)
        item = await asyncio.to_thread(self._claim, item_id)
        if not item:
            row = await asyncio.to_thread(self._row, item_id)
            return json.loads(row["result"]) if row["status"] == "done" else self._queued(row)
        heartbeat = asyncio.create_task(self._heartbeat(item))
        try:
            await self._ensure_ready(item.target)
            result = await self.deliver(item)
            await asyncio.to_thread(self._finish, item, result)
            self._log(logging.INFO, "Локальная запись обработана и подтверждена в БД", item, item.target)
            return result
        except asyncio.CancelledError:
            await asyncio.to_thread(self._retry, item, "shutdown")
            raise
        except LeaseLost:
            return self._queued(row)
        except Exception as exc:
            # Do not log exception text: connection strings and payloads can contain secrets.
            error = type(exc).__name__
            self.database_unavailable(item.target)
            await asyncio.to_thread(self._retry, item, error)
            self._log(logging.ERROR, "Заказ остаётся локально: запись в БД не завершена, будет повтор", item, item.target, error)
            self._wake.set()
            return self._queued(row)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, LeaseLost):
                await heartbeat

    def _pending_rows(self, target=None, site=None):
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM webhook_outbox WHERE status <> 'done'
                    AND (? IS NULL OR target=?) AND (? IS NULL OR site=?)
                ORDER BY id
            """, (target, target, site, site)).fetchall()
            return [dict(row) for row in rows]

    async def pending_submissions(self, target, site):
        rows = await asyncio.to_thread(self._pending_rows, target, site)
        result = []
        for row in rows:
            submission, state = json.loads(row["submission"]), json.loads(row["state"])
            submission.update(storage_status="queued", queue_attempts=row["attempts"],
                              queue_error=row["last_error"], im_number=state.get("im_number", ""))
            result.append(submission)
        return result

    async def run_once(self):
        rows = await asyncio.to_thread(self._pending_rows)
        targets = self.targets | {row["target"] for row in rows}

        async def drain(target):
            if self.blocked_until.get(target, 0) > time.time():
                return
            try:
                await self._ensure_ready(target)
            except Exception as exc:
                self.database_unavailable(target)
                self._log(logging.WARNING, "БД недоступна, приложение продолжает принимать заказы локально",
                          target=target, error=type(exc).__name__)
                return
            eligible = [r for r in rows if r["target"] == target
                        and r["retry_at"] <= time.time() and r["lease_until"] <= time.time()]
            for row in eligible[:self.batch_size]:
                await self.process(row["id"])
                if self.blocked_until.get(target, 0) > time.time():
                    break

        await asyncio.gather(*(drain(target) for target in targets))
        # Keep only small deduplication receipts for one day; payloads were cleared by _finish.
        await asyncio.to_thread(self._cleanup)

    def _cleanup(self):
        with self._connect() as conn:
            conn.execute("DELETE FROM webhook_outbox WHERE status='done' AND completed_at<?", (time.time() - 86400,))

    async def _loop(self):
        while not self._stopping:
            self._wake.clear()
            delay = self.retry_seconds
            try:
                await self.run_once()
                for row in await asyncio.to_thread(self._pending_rows):
                    due = max(row["retry_at"], row["lease_until"], self.blocked_until.get(row["target"], 0))
                    delay = min(delay, max(0.1, due - time.time()))
            except Exception as exc:
                self._log(logging.ERROR, "Ошибка локальной очереди, задания не удаляются", error=type(exc).__name__)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def start(self, targets=()):
        await self.initialize()
        self.targets.update(targets)
        self._stopping = False
        self._log(logging.INFO, "Локальная очередь готова, фоновая доставка включена")
        self._worker = asyncio.create_task(self._loop(), name="tilda-outbox")

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
