"""Configuration and application lifecycle for the isolated APEX mail service."""

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from cryptography.fernet import Fernet

from config import config
from services.apex_email_queue import ApexEmailQueue, QueueUnavailable, log_state
from services.apex_email_smtp import MAILBOX, SmtpSettings, send_mail


CONFIGURATION_ISSUES = {
    "missing_service_secret": ("APEX_EMAIL_SERVICE_SECRET", "Не задан общий HMAC-секрет APEX и 2p2p.ru."),
    "service_secret_too_short": ("APEX_EMAIL_SERVICE_SECRET", "HMAC-секрет должен содержать минимум 32 символа."),
    "missing_storage_key": ("APEX_EMAIL_STORAGE_KEY", "Не задан отдельный ключ шифрования Fernet."),
    "invalid_storage_key": ("APEX_EMAIL_STORAGE_KEY", "Нужен ключ Fernet: 32 случайных байта в urlsafe-base64 (44 символа), не hex-строка."),
    "storage_key_reuses_service_secret": ("APEX_EMAIL_STORAGE_KEY", "Ключ хранения должен отличаться от HMAC-секрета."),
    "missing_smtp_password": ("SMTP_PASSWORD", "Не задан пароль приложения SMTP."),
    "unsupported_smtp_port": ("SMTP_PORT", "Поддерживаются только 587 (STARTTLS) и 465 (TLS)."),
    "missing_smtp_host": ("SMTP_HOST", "Не задан SMTP-хост."),
    "invalid_smtp_user": ("SMTP_USER", f"Для сервиса нужен ящик {MAILBOX}."),
    "invalid_smtp_from": ("SMTP_FROM", f"Укажите только адрес {MAILBOX}, без имени отправителя и угловых скобок."),
    "invalid_queue_path": ("APEX_EMAIL_QUEUE_PATH", "Нужен непустой путь к файлу постоянной очереди, не :memory:."),
    "queue_path_conflicts_with_webhooks": ("APEX_EMAIL_QUEUE_PATH", "Очередь писем должна находиться в отдельном файле от TILDA_QUEUE_PATH."),
}


class EmailServiceNotConfigured(Exception):
    def __init__(self, issues=()):
        self.issues = tuple(code for code in issues if code in CONFIGURATION_ISSUES)
        super().__init__("email_service_not_configured")

    def diagnostics(self):
        return [{"code": code, "setting": CONFIGURATION_ISSUES[code][0],
                 "message": CONFIGURATION_ISSUES[code][1]} for code in self.issues]


def configuration_issues(settings=config):
    """Return fixed codes only, never values or exception messages."""
    issues = []
    secret = settings.APEX_EMAIL_SERVICE_SECRET.get_secret_value() if settings.APEX_EMAIL_SERVICE_SECRET else ""
    key = settings.APEX_EMAIL_STORAGE_KEY.get_secret_value() if settings.APEX_EMAIL_STORAGE_KEY else ""
    if not secret:
        issues.append("missing_service_secret")
    elif len(secret) < 32:
        issues.append("service_secret_too_short")
    if not key:
        issues.append("missing_storage_key")
    else:
        try:
            Fernet(key.encode("ascii"))
        except (ValueError, UnicodeError):
            issues.append("invalid_storage_key")
        if key == secret:
            issues.append("storage_key_reuses_service_secret")
    if not settings.SMTP_PASSWORD.get_secret_value():
        issues.append("missing_smtp_password")
    if settings.SMTP_PORT not in (587, 465):
        issues.append("unsupported_smtp_port")
    if not settings.SMTP_HOST.strip():
        issues.append("missing_smtp_host")
    if settings.SMTP_USER != MAILBOX:
        issues.append("invalid_smtp_user")
    if settings.SMTP_FROM != MAILBOX:
        issues.append("invalid_smtp_from")
    try:
        if not settings.APEX_EMAIL_QUEUE_PATH.strip() or settings.APEX_EMAIL_QUEUE_PATH == ":memory:":
            issues.append("invalid_queue_path")
        elif Path(settings.APEX_EMAIL_QUEUE_PATH).resolve() == Path(settings.TILDA_QUEUE_PATH).resolve():
            issues.append("queue_path_conflicts_with_webhooks")
    except (OSError, ValueError, RuntimeError):
        issues.append("invalid_queue_path")
    return issues


@dataclass(frozen=True)
class ApexEmailService:
    secret: str = field(repr=False)
    queue: ApexEmailQueue


def create_service(settings=config):
    issues = configuration_issues(settings)
    if issues:
        raise EmailServiceNotConfigured(issues)
    secret = settings.APEX_EMAIL_SERVICE_SECRET.get_secret_value() if settings.APEX_EMAIL_SERVICE_SECRET else ""
    key = settings.APEX_EMAIL_STORAGE_KEY.get_secret_value() if settings.APEX_EMAIL_STORAGE_KEY else ""
    password = settings.SMTP_PASSWORD.get_secret_value()
    smtp = SmtpSettings(settings.SMTP_HOST, settings.SMTP_PORT, MAILBOX, password)
    try:
        queue = ApexEmailQueue(settings.APEX_EMAIL_QUEUE_PATH, key.encode("ascii"), partial(send_mail, settings=smtp))
    except (ValueError, UnicodeError, QueueUnavailable):
        raise EmailServiceNotConfigured(("invalid_queue_path",)) from None
    return ApexEmailService(secret, queue)


_service: ApexEmailService | None = None


def get_service():
    global _service
    if _service is None:
        _service = create_service()
    return _service


@asynccontextmanager
async def apex_email_lifespan():
    global _service
    try:
        service = get_service()
    except EmailServiceNotConfigured as exc:
        # Optional new integration must not prevent orders, chat or games starting.
        log_state("disabled", error=",".join(exc.issues) or "configuration_required", level=logging.ERROR)
        yield
        return
    service.queue.start()
    try:
        yield
    finally:
        await service.queue.stop()
        if _service is service:
            _service = None
