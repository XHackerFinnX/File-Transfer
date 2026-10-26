"""Encrypted durable SQLite task queue, shared by processes on one persistent disk.

The task and encrypted content are one committed row. Leases and token-fenced
updates coordinate workers. SMTP is at-least-once after an ambiguous disconnect.
"""

import asyncio
import errno
import hashlib
import hmac
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from cryptography.fernet import Fernet, InvalidToken

from services.apex_email_contract import ApexMail, EmailRequestError, parse_mail
from services.apex_email_smtp import classify_smtp_error

LOGGER = logging.getLogger("apex.email")
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    LOGGER.addHandler(handler)

MIGRATION = Path(__file__).resolve().parents[1] / "db/migrations/apex_email_001.sql"
RETENTION_SECONDS = 7 * 86400


def log_state(state, message_id="", kind="", attempts=0, error="", level=logging.INFO):
    # Only fixed codes, UUID, kind and counts; never log exceptions or payloads.
    LOGGER.log(level, "state=%s message_id=%s kind=%s attempts=%s error=%s",
               state, message_id, kind, attempts, error)


class QueueUnavailable(Exception):
    pass


def queue_error_code(exc):
    """Classify failures without exposing SQLite SQL, filesystem paths or payloads."""
    if isinstance(exc, QueueUnavailable):
        code = exc.args[0] if exc.args else ""
        if code in ("storage_key_changed", "persistent_path_required"):
            return code
    if isinstance(exc, OSError):
        if exc.errno in (errno.EACCES, errno.EPERM):
            return "queue_permission_denied"
        if exc.errno == errno.EROFS:
            return "queue_read_only"
        if exc.errno in (errno.ENOSPC, errno.EDQUOT):
            return "queue_disk_full"
        if exc.errno in (errno.ENOTDIR, errno.EISDIR, errno.EINVAL):
            return "queue_path_invalid"
        if exc.errno == errno.ENOENT:
            return "queue_file_or_migration_missing"
    if isinstance(exc, sqlite3.Error):
        # Extended SQLite result codes have the primary error in their low byte.
        code = getattr(exc, "sqlite_errorcode", 0) & 0xff
        return {
            sqlite3.SQLITE_READONLY: "queue_read_only", sqlite3.SQLITE_CANTOPEN: "queue_cannot_open",
            sqlite3.SQLITE_BUSY: "queue_busy", sqlite3.SQLITE_LOCKED: "queue_busy",
            sqlite3.SQLITE_FULL: "queue_disk_full", sqlite3.SQLITE_IOERR: "queue_io_error",
            sqlite3.SQLITE_CORRUPT: "queue_corrupt", sqlite3.SQLITE_NOTADB: "queue_not_database",
        }.get(code, "queue_database_error")
    return "queue_unavailable"


class LeaseLost(Exception):
    pass


@dataclass(frozen=True)
class QueueItem:
    message_id: str
    kind: str
    attempts: int
    token: str
    encrypted_body: bytes = field(repr=False)


class ApexEmailQueue:
    def __init__(self, path: str | Path, storage_key: bytes, send: Callable[[ApexMail], None],
                 lease_seconds=120, poll_seconds=1):
        if str(path) == ":memory:":
            raise QueueUnavailable("persistent_path_required")
        self.path = Path(path).resolve()
        self.cipher = Fernet(storage_key)
        self._key_id = hashlib.sha256(storage_key).hexdigest()
        self._tag_key = hmac.digest(storage_key, b"apex-email/recipient-tags/v1", "sha256")
        self.send = send
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self._initialized = False
        self._initialize_lock = threading.Lock()
        self._wake = asyncio.Event()
        self._stopping = False
        self._worker: asyncio.Task | None = None

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA secure_delete=ON")
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self):
        with self._initialize_lock:
            if self._initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            self.path.chmod(0o600)
            with self._connect() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript("BEGIN IMMEDIATE;\n" + MIGRATION.read_text(encoding="utf-8"))
                previous = conn.execute("SELECT value FROM apex_email_meta WHERE name='storage_key_id'").fetchone()
                if previous and previous["value"] != self._key_id:
                    if conn.execute("SELECT 1 FROM apex_email_queue WHERE encrypted_body IS NOT NULL LIMIT 1").fetchone():
                        raise QueueUnavailable("storage_key_changed")
                conn.execute("INSERT OR REPLACE INTO apex_email_meta(name,value) VALUES ('storage_key_id',?)", (self._key_id,))
            self._initialized = True

    def enqueue(self, mail: ApexMail, raw: bytes):
        self.initialize()
        body_hash = hashlib.sha256(raw).hexdigest()
        tag = hmac.new(self._tag_key, mail.to.casefold().encode("utf-8"), hashlib.sha256).hexdigest()
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT body_sha256,status FROM apex_email_queue WHERE message_id=?",
                                    (mail.canonical_id,)).fetchone()
            if existing:
                if existing["body_sha256"] != body_hash:
                    raise EmailRequestError(409, "message_id_conflict")
                return False, existing["status"]
            if mail.expires_at.timestamp() <= now:
                raise EmailRequestError(410, "message_expired")
            created = mail.created_at.timestamp()
            newer = conn.execute("""
                SELECT 1 FROM apex_email_queue WHERE recipient_tag=? AND kind=? AND created_at>? LIMIT 1
            """, (tag, mail.kind, created)).fetchone()
            status = "cancelled" if newer else "queued"
            # Cancel only jobs that have not started SMTP, including sleeping retries.
            conn.execute("""
                UPDATE apex_email_queue SET status='cancelled', encrypted_body=NULL,
                    completed_at=?, last_error='superseded', retry_at=0
                WHERE recipient_tag=? AND kind=? AND created_at<? AND status IN ('queued','retry')
            """, (now, tag, mail.kind, created))
            conn.execute("""
                INSERT INTO apex_email_queue(message_id,body_sha256,kind,recipient_tag,created_at,
                    expires_at,received_at,status,encrypted_body,completed_at,last_error)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (mail.canonical_id, body_hash, mail.kind, tag, created, mail.expires_at.timestamp(), now,
                  status, self.cipher.encrypt(raw) if not newer else None, now if newer else 0,
                  "superseded" if newer else ""))
        # The context manager has committed both the task and its encrypted content.
        log_state(status, mail.canonical_id, mail.kind)
        return True, status

    def _claim(self):
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                UPDATE apex_email_queue SET status='expired',encrypted_body=NULL,completed_at=?,
                    lease_token='',lease_until=0,last_error='expired'
                WHERE expires_at<=? AND (status IN ('queued','retry') OR (status='sending' AND lease_until<=?))
            """, (now, now, now))
            conn.execute("""
                UPDATE apex_email_queue AS old SET status='cancelled',encrypted_body=NULL,
                    completed_at=?,lease_token='',lease_until=0,last_error='superseded'
                WHERE old.status='sending' AND old.lease_until<=? AND EXISTS (
                    SELECT 1 FROM apex_email_queue AS newer WHERE newer.recipient_tag=old.recipient_tag
                        AND newer.kind=old.kind AND newer.created_at>old.created_at
                )
            """, (now, now))
            conn.execute("DELETE FROM apex_email_queue WHERE completed_at>0 AND completed_at<?",
                         (now - RETENTION_SECONDS,))
            row = conn.execute("""
                SELECT * FROM apex_email_queue
                WHERE expires_at>? AND ((status IN ('queued','retry') AND retry_at<=?)
                    OR (status='sending' AND lease_until<=?))
                ORDER BY retry_at,received_at LIMIT 1
            """, (now, now, now)).fetchone()
            if not row:
                return None
            token = uuid.uuid4().hex
            conn.execute("""
                UPDATE apex_email_queue SET status='sending',lease_token=?,lease_until=?,attempts=attempts+1
                WHERE message_id=?
            """, (token, now + self.lease_seconds, row["message_id"]))
            return QueueItem(row["message_id"], row["kind"], row["attempts"] + 1, token, row["encrypted_body"])

    def _renew(self, item):
        with self._connect() as conn:
            updated = conn.execute("""
                UPDATE apex_email_queue SET lease_until=? WHERE message_id=? AND lease_token=? AND status='sending'
            """, (time.time() + self.lease_seconds, item.message_id, item.token)).rowcount
            if not updated:
                raise LeaseLost()

    def _finish(self, item, state, error="", retry_at=0):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if state == "retry" and conn.execute("""
                SELECT 1 FROM apex_email_queue AS newer JOIN apex_email_queue AS old
                    ON newer.recipient_tag=old.recipient_tag AND newer.kind=old.kind
                WHERE old.message_id=? AND newer.created_at>old.created_at LIMIT 1
            """, (item.message_id,)).fetchone():
                state, error, retry_at = "cancelled", "superseded", 0
            updated = conn.execute("""
                UPDATE apex_email_queue SET status=?,last_error=?,retry_at=?,lease_token='',lease_until=0,
                    encrypted_body=CASE WHEN ?='retry' THEN encrypted_body ELSE NULL END,
                    completed_at=CASE WHEN ?='retry' THEN 0 ELSE ? END
                WHERE message_id=? AND lease_token=? AND status='sending'
            """, (state, error, retry_at, state, state, time.time(), item.message_id, item.token)).rowcount
            if not updated:
                raise LeaseLost()
        log_state(state, item.message_id, item.kind, item.attempts, error,
                  logging.ERROR if state == "failed" else logging.WARNING if state == "retry" else logging.INFO)

    async def _heartbeat(self, item):
        while True:
            await asyncio.sleep(self.lease_seconds / 3)
            await asyncio.to_thread(self._renew, item)

    async def _deliver(self, item):
        heartbeat = asyncio.create_task(self._heartbeat(item))
        try:
            try:
                mail = parse_mail(self.cipher.decrypt(item.encrypted_body))
            except (InvalidToken, EmailRequestError, TypeError):
                await asyncio.to_thread(self._finish, item, "failed", "stored_payload_invalid")
                return
            if mail.expires_at.timestamp() <= time.time():
                await asyncio.to_thread(self._finish, item, "expired", "expired")
                return
            # Renew/fence once more immediately before handing off to SMTP.
            await asyncio.to_thread(self._renew, item)
            try:
                await asyncio.to_thread(self.send, mail)
            except Exception as exc:
                failure = classify_smtp_error(exc)
                now = time.time()
                if failure.code == "expired" or mail.expires_at.timestamp() <= now:
                    state, code, due = "expired", "expired", 0
                elif failure.temporary:
                    # 5, 10, 20, 40, 80, 160, 300... seconds, bounded by original expiry.
                    due = min(now + min(300, 5 * 2 ** min(item.attempts - 1, 6)), mail.expires_at.timestamp())
                    state, code = "retry", failure.code
                else:
                    state, code, due = "failed", failure.code, 0
                await asyncio.to_thread(self._finish, item, state, code, due)
            else:
                await asyncio.to_thread(self._finish, item, "sent")
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def run_once(self, batch_size=20):
        await asyncio.to_thread(self.initialize)
        for _ in range(batch_size):
            if self._stopping:
                break
            item = await asyncio.to_thread(self._claim)
            if item is None:
                break
            await self._deliver(item)

    async def _loop(self):
        while not self._stopping:
            self._wake.clear()
            try:
                await self.run_once()
            except Exception as exc:
                # SQLite errors can include paths; SMTP errors can contain recipient/body.
                log_state("worker_waiting", error=queue_error_code(exc), level=logging.ERROR)
            if not self._stopping:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)

    def start(self):
        self._stopping = False
        self._worker = asyncio.create_task(self._loop())

    def wake(self):
        self._wake.set()

    async def stop(self):
        self._stopping = True
        self._wake.set()
        if self._worker:
            # Do not release a lease while an uncancellable SMTP thread is still sending.
            await self._worker
            self._worker = None

    def status(self, limit=50):
        """Safe operator view: no recipient, encrypted body, tags, tokens or hashes."""
        self.initialize()
        with self._connect() as conn:
            counts = {r["status"]: r["n"] for r in conn.execute(
                "SELECT status,count(*) AS n FROM apex_email_queue GROUP BY status")}
            rows = conn.execute("""
                SELECT message_id,kind,status,attempts,last_error,created_at,expires_at,retry_at
                FROM apex_email_queue ORDER BY received_at DESC LIMIT ?
            """, (limit,)).fetchall()
            return {"counts": counts, "messages": [dict(row) for row in rows]}
