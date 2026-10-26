"""Signed APEX mail contract, durable multi-worker delivery; SMTP is always mocked."""

import asyncio
import errno
import hashlib
import hmac
import json
import multiprocessing
import os
import smtplib
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import ExitStack, asynccontextmanager, contextmanager
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI
from pydantic import SecretStr

from tests.test_tilda_webhooks import TEST_ENV

with patch.dict(os.environ, TEST_ENV):
    from routers import apex_email as endpoint
    from services import apex_email as runtime

from services.apex_email_contract import MAX_BODY, parse_mail, verify_signature
from services.apex_email_queue import ApexEmailQueue, LeaseLost, QueueUnavailable
from services.apex_email_smtp import MAILBOX, SmtpSettings, send_mail

SECRET = "тестовый-секрет-с-подписью-" + "1" * 40


def mail_payload(kind="registration", **overrides):
    now = datetime.now(timezone.utc)
    reset = "https://apextop.ru/account#reset-password=Example_TOKEN-0123456789"
    body = {
        "schema_version": 1, "message_id": str(uuid.uuid4()), "source": "apex",
        "site_url": "https://apextop.ru", "kind": kind, "to": "buyer@example.test",
        "subject": "Подтверди почту — APEX", "text": "Код: 012345. Привет!",
        "html": '<html lang="ru"><body><strong>012345</strong></body></html>',
        "created_at": now.isoformat(), "expires_at": (now + timedelta(minutes=10 if kind == "registration" else 30)).isoformat(),
        "data": {"name": "Александр", "code": "012345"} if kind == "registration" else {"name": "Александр", "reset_url": reset},
        "request_context": {} if kind == "registration" else {
            "ip": "203.0.113.4", "location": "местоположение недоступно", "user_agent": "Test / Android",
            "requested_at": now.isoformat(),
        },
    }
    if kind == "password_reset":
        body.update(text=f"Восстановление: {reset}", html=f'<a href="{reset}">Восстановить</a>')
    return {**body, **overrides}


def raw_body(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def signed_headers(raw, message_id, timestamp=None):
    timestamp = str(int(time.time())) if timestamp is None else str(timestamp)
    return {
        "Content-Type": "application/json; charset=utf-8", "X-Apex-Timestamp": timestamp,
        "X-Apex-Signature": "sha256=" + hmac.new(SECRET.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest(),
        "Idempotency-Key": "apex-mail-" + message_id,
    }


def process_request_and_deliver(path, storage_key, raw, signature, timestamp, delivery_log):
    """Separate worker process: real signature, SQLite transaction and lease, fake SMTP."""
    verify_signature(SECRET, timestamp, signature, raw)

    def record_delivery(mail):
        with open(delivery_log, "ab") as handle:
            handle.write((mail.canonical_id + "\n").encode())

    box = ApexEmailQueue(path, storage_key, record_delivery)
    accepted = box.enqueue(parse_mail(raw), raw)
    asyncio.run(box.run_once())
    return accepted


class EmailTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.path = self.directory / "private" / "email.sqlite3"
        self.key = Fernet.generate_key()
        self.smtp = Mock()
        self.box = self.new_box()
        self.stack.enter_context(patch.object(endpoint, "get_service", return_value=runtime.ApexEmailService(SECRET, self.box)))
        app = FastAPI()
        app.include_router(endpoint.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test.invalid")
        self.addAsyncCleanup(self.client.aclose)
        self.addAsyncCleanup(self.box.stop)

    def new_box(self, **kwargs):
        return ApexEmailQueue(self.path, self.key, self.smtp, **kwargs)

    async def post(self, payload=None, raw=None, headers=None):
        payload = payload or mail_payload()
        raw = raw if raw is not None else raw_body(payload)
        return await self.client.post("/api/apex/email", content=raw,
                                      headers=headers or signed_headers(raw, payload["message_id"]))

    def row(self, message_id):
        with self.box._connect() as conn:
            row = conn.execute("SELECT * FROM apex_email_queue WHERE message_id=?", (message_id,)).fetchone()
            return dict(row) if row else None

    async def test_signed_utf8_registration_and_reset_preserve_content(self):
        for kind in ("registration", "password_reset"):
            payload = mail_payload(kind)
            response = await self.post(payload)
            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(response.json(), {"ok": True, "message_id": payload["message_id"], "state": "queued"})
            stored = self.row(payload["message_id"])
            self.assertEqual(self.box.cipher.decrypt(stored["encrypted_body"]), raw_body(payload))
            await self.box.run_once()
            delivered = self.smtp.call_args.args[0]
            self.assertEqual(delivered.text, payload["text"])
            self.assertEqual(delivered.html, payload["html"])
            self.assertEqual(delivered.data.model_dump(exclude_none=True), payload["data"])
            self.assertEqual(self.row(payload["message_id"])["status"], "sent")
            self.assertIsNone(self.row(payload["message_id"])["encrypted_body"])
        self.assertEqual(self.smtp.call_count, 2)

    async def test_signature_checked_before_json_and_uses_exact_raw_bytes(self):
        payload = mail_payload()
        raw = raw_body(payload)
        variants = [
            (raw + b" ", signed_headers(raw, payload["message_id"])),
            (raw, {**signed_headers(raw, payload["message_id"]), "X-Apex-Signature": "sha256=" + "0" * 64}),
            (raw, signed_headers(raw, payload["message_id"], int(time.time()) - 301)),
            (raw, signed_headers(raw, payload["message_id"], int(time.time()) + 301)),
            (raw, signed_headers(raw, payload["message_id"], int(time.time()) * 1000)),
            (raw, signed_headers(raw, payload["message_id"], "+123")),
            (b"not json", signed_headers(raw, payload["message_id"])),
            (raw, {**signed_headers(raw, payload["message_id"]), "X-Apex-Signature": "SHA256=" + "a" * 64}),
        ]
        for body, headers in variants:
            response = await self.post(payload, body, headers)
            self.assertEqual(response.status_code, 401, response.text)
            self.assertFalse(response.json()["ok"])
        self.assertFalse(self.path.exists())
        self.smtp.assert_not_called()

    async def test_signature_header_ambiguity_is_rejected(self):
        payload = mail_payload()
        raw = raw_body(payload)
        headers = list(signed_headers(raw, payload["message_id"]).items())
        headers.append(("X-Apex-Timestamp", str(int(time.time()))))
        self.assertEqual((await self.post(payload, raw, headers)).status_code, 401)

    async def test_validation_rejects_bad_contract_without_sensitive_errors(self):
        payload = mail_payload()
        changes = [
            {"schema_version": True}, {"schema_version": "1"}, {"schema_version": 2},
            {"message_id": "bad-uuid"}, {"source": "other"}, {"site_url": "https://other.test"},
            {"kind": "newsletter"}, {"to": "one@example.test,two@example.test"},
            {"to": "Name <one@example.test>"}, {"to": "one@example.test\r\nBcc: two@example.test"},
            {"subject": "bad\nheader"}, {"subject": "s" * 201}, {"text": "  "}, {"html": ""},
            {"data": {"code": 12345}}, {"data": {"code": "12345"}}, {"data": {"code": "１２３４５６"}},
            {"data": {"code": "012345", "name": "n" * 121}},
            {"created_at": "2026-01-01T00:00:00"}, {"expires_at": 1793000000},
            {"created_at": (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()},
            {"expires_at": (datetime.now(timezone.utc) + timedelta(minutes=12)).isoformat()},
            {"from": "attacker@example.test"}, {"attachments": []},
        ]
        for change in changes:
            response = await self.post({**payload, **change})
            self.assertEqual(response.status_code, 422, (list(change), response.text))
            self.assertEqual(response.json(), {"ok": False, "error": "invalid_payload"})
        self.smtp.assert_not_called()

    async def test_reset_links_are_exact_and_never_fetched(self):
        payload = mail_payload("password_reset")
        for url in (
            "http://apextop.ru/account#reset-password=token", "https://evil.test/account#reset-password=token",
            "https://user@apextop.ru/account#reset-password=token", "https://apextop.ru:443/account#reset-password=token",
            "https://apextop.ru.evil.test/account#reset-password=token", "https://apextop.ru/account/?reset-password=token",
            "https://apextop.ru/account?x=y#reset-password=token", "https://apextop.ru/account#reset-password=token&x=y",
            "https://apextop.ru/account#reset-password=", "https://apextop.ru/account#reset-password=token%20encoded",
        ):
            response = await self.post({**payload, "data": {"reset_url": url}})
            self.assertEqual(response.status_code, 422, url)
        self.smtp.assert_not_called()
        # The token is opaque; URI-unreserved dots/tilde must not impose a token format.
        url = "https://apextop.ru/account#reset-password=opaque.part~token-0123"
        self.assertEqual((await self.post({**payload, "data": {"reset_url": url}})).status_code, 202)

    async def test_json_limits_content_types_and_chunked_body(self):
        payload = mail_payload()
        raw = raw_body(payload)
        for content_type in ("text/plain", "application/json; charset=latin1", "application/json; extra=value"):
            response = await self.post(payload, raw, {**signed_headers(raw, payload["message_id"]), "Content-Type": content_type})
            self.assertEqual(response.status_code, 415)
        for invalid in (b"not JSON", b'{"schema_version":1,"schema_version":1}', b'{"x":NaN}', b"\xff"):
            response = await self.post(payload, invalid)
            self.assertEqual(response.status_code, 422)
        oversized = b"x" * (MAX_BODY + 1)
        self.assertEqual((await self.post(payload, oversized)).status_code, 413)

        async def chunks():
            yield b"x" * MAX_BODY
            yield b"y"

        response = await self.client.post("/api/apex/email", content=chunks(), headers=signed_headers(raw, payload["message_id"]))
        self.assertEqual(response.status_code, 413)
        self.assertFalse(self.path.exists())

    async def test_idempotency_exact_body_and_lost_http_response(self):
        payload = mail_payload()
        self.assertEqual((await self.post(payload)).status_code, 202)
        # Client did not see the first reply and resends with a fresh signature.
        duplicate = await self.post(payload)
        self.assertEqual(duplicate.status_code, 200)
        self.assertIs(duplicate.json()["duplicate"], True)
        await self.box.run_once()
        self.assertEqual((await self.post(payload)).status_code, 200)
        self.smtp.assert_called_once()
        changed = {**payload, "subject": "Different"}
        self.assertEqual((await self.post(changed)).status_code, 409)
        different_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.assertEqual((await self.post(payload, different_bytes)).status_code, 409)
        wrong = {**signed_headers(raw_body(payload), payload["message_id"]), "Idempotency-Key": "apex-mail-other"}
        self.assertEqual((await self.post(payload, headers=wrong)).status_code, 422)

    async def test_parallel_posts_and_independent_workers_create_one_task(self):
        payload = mail_payload()
        responses = await asyncio.gather(*(self.post(payload) for _ in range(12)))
        self.assertEqual(sum(r.status_code == 202 for r in responses), 1)
        self.assertEqual(sum(r.status_code == 200 for r in responses), 11)
        # Separate queue instances represent distinct Uvicorn processes on the same disk.
        another = self.new_box()
        await asyncio.gather(self.box.run_once(), another.run_once())
        self.smtp.assert_called_once()
        self.assertEqual(self.box.status()["counts"], {"sent": 1})

    async def test_parallel_different_bodies_with_same_id_conflict(self):
        payload = mail_payload()
        responses = await asyncio.gather(self.post(payload), self.post({**payload, "text": "Different body"}))
        self.assertEqual(sorted(r.status_code for r in responses), [202, 409])

    async def test_independent_processes_validate_utf8_and_share_one_durable_task(self):
        payload = mail_payload()
        raw = raw_body(payload)
        headers = signed_headers(raw, payload["message_id"])
        log_path = str(self.directory / "mock-smtp.log")
        args = (str(self.path), self.key, raw, headers["X-Apex-Signature"], headers["X-Apex-Timestamp"], log_path)
        with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as pool:
            loop = asyncio.get_running_loop()
            results = await asyncio.gather(*(loop.run_in_executor(pool, process_request_and_deliver, *args) for _ in range(2)))
        self.assertEqual(sum(created for created, state in results), 1)
        self.assertEqual(Path(log_path).read_text().splitlines(), [payload["message_id"]])

    async def test_missing_secret_returns_safe_503(self):
        with patch.object(endpoint, "get_service", side_effect=runtime.EmailServiceNotConfigured):
            response = await self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"ok": False, "error": "email_service_not_configured"})
        self.smtp.assert_not_called()

    async def test_configuration_failure_logs_exact_issue_without_public_disclosure(self):
        failure = runtime.EmailServiceNotConfigured(("missing_storage_key",))
        with patch.object(endpoint, "get_service", side_effect=failure), self.assertLogs("apex.email", level="ERROR") as logs:
            response = await self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"ok": False, "error": "email_service_not_configured"})
        self.assertIn("missing_storage_key", " ".join(logs.output))
        self.assertNotIn(SECRET, " ".join(logs.output))

    async def test_read_only_queue_failure_has_safe_server_reason(self):
        with patch.object(self.box, "enqueue", side_effect=PermissionError(errno.EACCES, "private-path-and-secret")), \
                self.assertLogs("apex.email", level="ERROR") as logs:
            response = await self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"ok": False, "error": "queue_unavailable"})
        self.assertIn("queue_permission_denied", " ".join(logs.output))
        self.assertNotIn("private-path-and-secret", " ".join(logs.output))
        self.smtp.assert_not_called()

    async def test_wrong_storage_key_is_identified_and_does_not_replace_pending_mail(self):
        payload = mail_payload()
        await self.post(payload)
        wrong = ApexEmailQueue(self.path, Fernet.generate_key(), self.smtp)
        with patch.object(endpoint, "get_service", return_value=runtime.ApexEmailService(SECRET, wrong)), \
                self.assertLogs("apex.email", level="ERROR") as logs:
            response = await self.post(mail_payload())
        self.assertEqual(response.status_code, 503)
        self.assertIn("storage_key_changed", " ".join(logs.output))
        await self.box.run_once()
        self.smtp.assert_called_once()
        self.assertEqual(self.smtp.call_args.args[0].message_id, payload["message_id"])

    async def test_persistence_restart_encryption_and_key_mismatch(self):
        payload = mail_payload()
        self.assertEqual((await self.post(payload)).status_code, 202)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        for path in self.path.parent.iterdir():
            stored = path.read_bytes()
            for sensitive in (b"012345", b"buyer@example.test", payload["html"].encode()):
                self.assertNotIn(sensitive, stored)
        wrong_key = ApexEmailQueue(self.path, Fernet.generate_key(), self.smtp)
        with self.assertRaises(QueueUnavailable):
            wrong_key.initialize()
        restarted = self.new_box()
        await restarted.run_once()
        self.smtp.assert_called_once()
        self.assertIsNone(self.row(payload["message_id"])["encrypted_body"])

    async def test_commit_failure_never_acknowledges(self):
        self.box.initialize()
        real_connect = self.box._connect

        @contextmanager
        def failing_commit():
            with real_connect() as conn:
                yield conn
                raise sqlite3.OperationalError("private SQL body and password")

        with patch.object(self.box, "_connect", failing_commit), self.assertLogs("apex.email", level="ERROR") as logs:
            response = await self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Retry-After"], "60")
        self.assertNotIn("private SQL", " ".join(logs.output))
        self.assertEqual(self.box.status()["counts"], {})
        self.smtp.assert_not_called()

    async def test_new_expired_is_410_but_identical_expired_duplicate_is_200(self):
        now = datetime.now(timezone.utc)
        expired = mail_payload(created_at=(now - timedelta(minutes=11)).isoformat(), expires_at=(now - timedelta(minutes=1)).isoformat())
        self.assertEqual((await self.post(expired)).status_code, 410)
        payload = mail_payload(expires_at=(now + timedelta(seconds=2)).isoformat())
        self.assertEqual((await self.post(payload)).status_code, 202)
        with patch("time.time", return_value=time.time() + 3):
            response = await self.post(payload)
            self.assertEqual(response.status_code, 200)
            await self.box.run_once()
        row = self.row(payload["message_id"])
        self.assertEqual(row["status"], "expired")
        self.assertIsNone(row["encrypted_body"])
        self.smtp.assert_not_called()

    async def test_transient_smtp_backoff_survives_restart_without_logging_secrets(self):
        payload = mail_payload()
        await self.post(payload)
        self.smtp.side_effect = smtplib.SMTPDataError(451, b"012345 buyer@example.test smtp-password")
        with self.assertLogs("apex.email", level="WARNING") as logs:
            await self.box.run_once()
        row = self.row(payload["message_id"])
        self.assertEqual((row["status"], row["attempts"], row["last_error"]), ("retry", 1, "smtp_451"))
        self.assertGreater(row["retry_at"], time.time())
        self.assertNotIn("012345", " ".join(logs.output))
        self.assertNotIn(payload["to"], " ".join(logs.output))
        await self.box.run_once()
        self.assertEqual(self.smtp.call_count, 1)
        self.smtp.side_effect = None
        with self.box._connect() as conn:
            conn.execute("UPDATE apex_email_queue SET retry_at=0")
        await self.new_box().run_once()
        self.assertEqual(self.smtp.call_count, 2)
        self.assertEqual(self.row(payload["message_id"])["status"], "sent")

    async def test_permanent_failure_is_safe_visible_and_clears_payload(self):
        payload = mail_payload("password_reset")
        await self.post(payload)
        self.smtp.side_effect = smtplib.SMTPRecipientsRefused({payload["to"]: (550, b"token Example_TOKEN-0123456789")})
        with self.assertLogs("apex.email", level="ERROR") as logs:
            await self.box.run_once()
        status = self.box.status()
        self.assertEqual(status["counts"], {"failed": 1})
        self.assertEqual(status["messages"][0]["last_error"], "smtp_recipient_refused")
        self.assertIsNone(self.row(payload["message_id"])["encrypted_body"])
        safe = json.dumps(status) + " ".join(logs.output)
        for sensitive in (payload["to"], payload["html"], "Example_TOKEN", "recipient_tag", "body_sha256"):
            self.assertNotIn(sensitive, safe)

    async def test_newer_code_cancels_unsent_older_code_and_out_of_order_request(self):
        now = datetime.now(timezone.utc)
        old = mail_payload(created_at=(now - timedelta(seconds=10)).isoformat())
        newer = mail_payload(created_at=now.isoformat())
        await self.post(old)
        await self.post(newer)
        self.assertEqual(self.row(old["message_id"])["status"], "cancelled")
        self.assertIsNone(self.row(old["message_id"])["encrypted_body"])
        await self.box.run_once()
        self.smtp.assert_called_once()
        self.assertEqual(self.smtp.call_args.args[0].message_id, newer["message_id"])
        oldest = mail_payload(created_at=(now - timedelta(seconds=20)).isoformat())
        response = await self.post(oldest)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["state"], "cancelled")
        self.assertIsNone(self.row(oldest["message_id"])["encrypted_body"])

    async def test_worker_lease_heartbeat_and_fenced_completion(self):
        self.box.lease_seconds = 0.18
        payload = mail_payload()
        await self.post(payload)
        started = threading.Event()

        def slow_smtp(mail):
            started.set()
            time.sleep(0.5)

        self.smtp.side_effect = slow_smtp
        first = asyncio.create_task(self.box.run_once())
        await asyncio.to_thread(started.wait, 2)
        await asyncio.sleep(0.3)
        await self.new_box(lease_seconds=0.18).run_once()
        await first
        self.smtp.assert_called_once()
        another = mail_payload(to="other@example.test")
        await self.post(another)
        claimed = self.box._claim()
        with self.box._connect() as conn:
            conn.execute("UPDATE apex_email_queue SET lease_token='another-worker' WHERE message_id=?", (claimed.message_id,))
        with self.assertRaises(LeaseLost):
            self.box._finish(claimed, "sent")

    async def test_abandoned_worker_lease_recovers_and_uses_same_message_id(self):
        payload = mail_payload()
        await self.post(payload)
        self.box._claim()  # Process stops after claiming, before recording SMTP outcome.
        with self.box._connect() as conn:
            conn.execute("UPDATE apex_email_queue SET lease_until=0")
        await self.new_box().run_once()
        self.smtp.assert_called_once()
        self.assertEqual(self.row(payload["message_id"])["attempts"], 2)

    async def test_deduplication_receipt_is_kept_for_at_least_seven_days(self):
        payload = mail_payload()
        await self.post(payload)
        await self.box.run_once()
        now = time.time()
        with patch("time.time", return_value=now + 7 * 86400 - 10):
            self.box._claim()
        self.assertIsNotNone(self.row(payload["message_id"]))
        with patch("time.time", return_value=now + 7 * 86400 + 10):
            self.box._claim()
        self.assertIsNone(self.row(payload["message_id"]))

    async def test_background_worker_delivers_and_shutdown_waits(self):
        self.box.poll_seconds = 0.01
        self.box.start()
        await self.post()
        for _ in range(100):
            if self.smtp.called:
                break
            await asyncio.sleep(0.01)
        await self.box.stop()
        self.smtp.assert_called_once()
        self.assertEqual(self.box.status()["counts"], {"sent": 1})

    async def test_smtp_failure_cannot_requeue_a_superseded_code(self):
        now = datetime.now(timezone.utc)
        old = mail_payload(created_at=(now - timedelta(seconds=10)).isoformat())
        await self.post(old)
        item = self.box._claim()
        await self.post(mail_payload(created_at=now.isoformat()))
        self.box._finish(item, "retry", "smtp_451", time.time() + 5)
        self.assertEqual(self.row(old["message_id"])["status"], "cancelled")
        self.assertIsNone(self.row(old["message_id"])["encrypted_body"])


class SmtpAndSettingsTests(unittest.TestCase):
    def test_operator_migration_and_status_commands_are_safe_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {**os.environ, **TEST_ENV, "APEX_EMAIL_SERVICE_SECRET": SECRET,
                           "APEX_EMAIL_STORAGE_KEY": Fernet.generate_key().decode(), "SMTP_PASSWORD": "test-only-password",
                           "SMTP_HOST": "smtp.yandex.com", "SMTP_PORT": "587", "SMTP_USER": MAILBOX, "SMTP_FROM": MAILBOX,
                           "APEX_EMAIL_QUEUE_PATH": str(Path(directory) / "email.sqlite3")}
            for command in ("migrate", "migrate", "status", "doctor"):
                result = subprocess.run([sys.executable, "-m", "scripts.apex_email_admin", command],
                                        cwd=Path(__file__).resolve().parents[1], env=environment,
                                        text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                status = json.loads(result.stdout)
                expected = {"status": {"counts": {}, "messages": []},
                            "migrate": {"ok": True, "schema_version": 1},
                            "doctor": {"ok": True, "configuration": "ready", "queue": "ready", "smtp": "not_tested"}}
                self.assertEqual(status, expected[command])
                for secret in (SECRET, environment["APEX_EMAIL_STORAGE_KEY"], environment["SMTP_PASSWORD"]):
                    self.assertNotIn(secret, result.stdout + result.stderr)

    def test_doctor_names_missing_or_invalid_key_without_printing_any_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {**os.environ, **TEST_ENV, "APEX_EMAIL_SERVICE_SECRET": SECRET,
                           "APEX_EMAIL_STORAGE_KEY": "", "SMTP_PASSWORD": "private-smtp-password",
                           "SMTP_HOST": "smtp.yandex.com", "SMTP_PORT": "587", "SMTP_USER": MAILBOX, "SMTP_FROM": MAILBOX,
                           "APEX_EMAIL_QUEUE_PATH": str(Path(directory) / "email.sqlite3")}
            for key, expected in (("", "missing_storage_key"), ("a" * 64, "invalid_storage_key")):
                environment["APEX_EMAIL_STORAGE_KEY"] = key
                result = subprocess.run([sys.executable, "-m", "scripts.apex_email_admin", "doctor"],
                                        cwd=Path(__file__).resolve().parents[1], env=environment,
                                        text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 1, result.stderr)
                diagnosis = json.loads(result.stdout)
                self.assertFalse(diagnosis["ok"])
                self.assertEqual(diagnosis["issues"][0]["code"], expected)
                self.assertEqual(diagnosis["issues"][0]["setting"], "APEX_EMAIL_STORAGE_KEY")
                for secret in (SECRET, environment["SMTP_PASSWORD"], "a" * 64):
                    self.assertNotIn(secret, result.stdout + result.stderr)
            self.assertFalse((Path(directory) / "email.sqlite3").exists())

    def test_full_application_lifecycle_serves_game_and_sends_signed_mail_without_redirect(self):
        from fastapi.testclient import TestClient
        import main

        @asynccontextmanager
        async def no_postgres():
            yield

        with tempfile.TemporaryDirectory() as directory:
            smtp = Mock()
            box = ApexEmailQueue(Path(directory) / "email.sqlite3", Fernet.generate_key(), smtp)
            service = runtime.ApexEmailService(SECRET, box)
            payload = mail_payload()
            raw = raw_body(payload)
            with patch.object(main, "tilda_storage_lifespan", no_postgres), patch.object(runtime, "_service", service):
                with TestClient(main.app, follow_redirects=False) as client:
                    response = client.post("/api/apex/email", content=raw, headers=signed_headers(raw, payload["message_id"]))
                    self.assertEqual(response.status_code, 202)
                    self.assertIs(response.json()["ok"], True)
                    self.assertEqual(response.json()["message_id"], payload["message_id"])
                    self.assertNotIn("location", response.headers)
                    self.assertEqual(response.headers["cache-control"], "no-store")
                    self.assertEqual(client.get("/volleyball/online/").status_code, 200)
                    deadline = time.monotonic() + 2
                    while not smtp.called and time.monotonic() < deadline:
                        time.sleep(0.01)
                smtp.assert_called_once()
                self.assertEqual(box.status()["counts"], {"sent": 1})

    def test_multipart_starttls_verified_before_login_and_deterministic_id(self):
        payload = mail_payload("password_reset")
        mail = parse_mail(raw_body(payload))
        settings = SmtpSettings("smtp.yandex.com", 587, MAILBOX, "test-only-password")
        with patch("services.apex_email_smtp.smtplib.SMTP") as smtp, patch("services.apex_email_smtp.smtplib.SMTP_SSL") as implicit:
            server = smtp.return_value
            server.send_message.return_value = {}
            send_mail(mail, settings)
            implicit.assert_not_called()
            self.assertLess([c[0] for c in server.mock_calls].index("starttls"), [c[0] for c in server.mock_calls].index("login"))
            tls = server.starttls.call_args.kwargs["context"]
            self.assertTrue(tls.check_hostname)
            self.assertEqual(tls.verify_mode, ssl.CERT_REQUIRED)
            message = server.send_message.call_args.args[0]
            self.assertEqual(str(message["From"]), f"APEX <{MAILBOX}>")
            self.assertEqual(message["Message-ID"], f"<apex.{mail.message_id}@2p2p.ru>")
            self.assertEqual(message.get_content_type(), "multipart/alternative")
            self.assertEqual(message.get_body(preferencelist=("plain",)).get_content().rstrip("\r\n"), payload["text"])
            self.assertEqual(message.get_body(preferencelist=("html",)).get_content().rstrip("\r\n"), payload["html"])
            self.assertEqual(server.send_message.call_args.kwargs["to_addrs"], [payload["to"]])
            server.set_debuglevel.assert_called_once_with(0)
            self.assertIsNone(message["Reply-To"])

    def test_implicit_tls_and_tls_failure_never_authenticate_in_plaintext(self):
        mail = parse_mail(raw_body(mail_payload()))
        with patch("services.apex_email_smtp.smtplib.SMTP_SSL") as smtp:
            smtp.return_value.send_message.return_value = {}
            send_mail(mail, SmtpSettings("smtp.yandex.com", 465, MAILBOX, "test-only-password"))
            self.assertTrue(smtp.call_args.kwargs["context"].check_hostname)
            smtp.return_value.starttls.assert_not_called()
        with patch("services.apex_email_smtp.smtplib.SMTP") as smtp:
            smtp.return_value.starttls.side_effect = ssl.SSLCertVerificationError("private data")
            from services.apex_email_smtp import DeliveryFailure
            with self.assertRaises(DeliveryFailure) as failure:
                send_mail(mail, SmtpSettings("smtp.yandex.com", 587, MAILBOX, "test-only-password"))
            self.assertEqual(failure.exception.code, "smtp_tls_certificate")
            smtp.return_value.login.assert_not_called()
            smtp.return_value.send_message.assert_not_called()

    def test_optional_settings_fail_closed_without_breaking_other_services(self):
        good = runtime.config.model_copy(update={
            "APEX_EMAIL_SERVICE_SECRET": SecretStr(SECRET), "APEX_EMAIL_STORAGE_KEY": SecretStr(Fernet.generate_key().decode()),
            "SMTP_PASSWORD": SecretStr("test-only-password"),
        })
        self.assertEqual(runtime.create_service(good).secret, SECRET)
        for change in (
            {"APEX_EMAIL_SERVICE_SECRET": None}, {"APEX_EMAIL_SERVICE_SECRET": SecretStr("")},
            {"APEX_EMAIL_SERVICE_SECRET": SecretStr("short")}, {"APEX_EMAIL_STORAGE_KEY": None},
            {"APEX_EMAIL_STORAGE_KEY": SecretStr("invalid-key")}, {"SMTP_PASSWORD": SecretStr("")},
            {"SMTP_PORT": 25}, {"SMTP_FROM": "other@example.test"}, {"SMTP_USER": "other@example.test"},
            {"APEX_EMAIL_QUEUE_PATH": good.TILDA_QUEUE_PATH},
            {"APEX_EMAIL_SERVICE_SECRET": good.APEX_EMAIL_STORAGE_KEY},
        ):
            with self.assertRaises(runtime.EmailServiceNotConfigured):
                runtime.create_service(good.model_copy(update=change))


if __name__ == "__main__":
    unittest.main()
