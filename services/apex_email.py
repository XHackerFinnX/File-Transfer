"""Configuration and application lifecycle for the isolated APEX mail service."""

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from config import config
from services.apex_email_queue import ApexEmailQueue, QueueUnavailable, log_state
from services.apex_email_smtp import MAILBOX, SmtpSettings, send_mail


class EmailServiceNotConfigured(Exception):
    pass


@dataclass(frozen=True)
class ApexEmailService:
    secret: str = field(repr=False)
    queue: ApexEmailQueue


def create_service(settings=config):
    secret = settings.APEX_EMAIL_SERVICE_SECRET.get_secret_value() if settings.APEX_EMAIL_SERVICE_SECRET else ""
    key = settings.APEX_EMAIL_STORAGE_KEY.get_secret_value() if settings.APEX_EMAIL_STORAGE_KEY else ""
    password = settings.SMTP_PASSWORD.get_secret_value()
    if (len(secret) < 32 or not key or key == secret or not password
            or settings.SMTP_PORT not in (587, 465) or not settings.SMTP_HOST.strip()
            or settings.SMTP_USER != MAILBOX or settings.SMTP_FROM != MAILBOX
            or Path(settings.APEX_EMAIL_QUEUE_PATH).resolve() == Path(settings.TILDA_QUEUE_PATH).resolve()):
        raise EmailServiceNotConfigured()
    smtp = SmtpSettings(settings.SMTP_HOST, settings.SMTP_PORT, MAILBOX, password)
    try:
        queue = ApexEmailQueue(settings.APEX_EMAIL_QUEUE_PATH, key.encode("ascii"), partial(send_mail, settings=smtp))
    except (ValueError, UnicodeError, QueueUnavailable):
        raise EmailServiceNotConfigured() from None
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
    except EmailServiceNotConfigured:
        # Optional new integration must not prevent orders, chat or games starting.
        log_state("disabled", error="configuration_required")
        yield
        return
    service.queue.start()
    try:
        yield
    finally:
        await service.queue.stop()
        if _service is service:
            _service = None
