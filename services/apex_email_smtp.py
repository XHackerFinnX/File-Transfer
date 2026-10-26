"""Transactional SMTP only; no body, recipient or provider error text is logged."""

import smtplib
import ssl
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime

from services.apex_email_contract import ApexMail

MAILBOX = "sewingnn.notification@2p2p.ru"


class DeliveryFailure(Exception):
    def __init__(self, code, temporary=True):
        self.code, self.temporary = code, temporary
        super().__init__(code)


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    user: str
    password: str = field(repr=False)


def build_message(mail: ApexMail):
    message = EmailMessage(policy=SMTP)
    message["From"] = f"APEX <{MAILBOX}>"
    message["To"] = mail.to
    message["Subject"] = mail.subject
    message["Date"] = format_datetime(mail.created_at)
    message["Message-ID"] = f"<apex.{mail.canonical_id}@2p2p.ru>"
    message.set_content(mail.text, charset="utf-8")
    message.add_alternative(mail.html, subtype="html", charset="utf-8")
    return message


def classify_smtp_error(exc):
    if isinstance(exc, DeliveryFailure):
        return exc
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = [int(value[0]) for value in exc.recipients.values()]
        return DeliveryFailure("smtp_recipient_refused", any(400 <= c < 500 for c in codes))
    if isinstance(exc, smtplib.SMTPResponseException):
        code = int(exc.smtp_code)
        return DeliveryFailure(f"smtp_{code}", 400 <= code < 500)
    if isinstance(exc, ssl.SSLCertVerificationError):
        return DeliveryFailure("smtp_tls_certificate", False)
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return DeliveryFailure("smtp_tls_or_auth_unavailable", False)
    if isinstance(exc, (OSError, smtplib.SMTPServerDisconnected)):
        return DeliveryFailure("smtp_connection_error")
    return DeliveryFailure("smtp_internal_error")


def send_mail(mail: ApexMail, settings: SmtpSettings):
    # Also check inside the thread immediately before starting a network attempt.
    if mail.expires_at.timestamp() <= time.time():
        raise DeliveryFailure("expired", False)
    message = build_message(mail)
    context = ssl.create_default_context()
    try:
        if settings.port == 465:
            server = smtplib.SMTP_SSL(settings.host, 465, timeout=20, context=context)
        else:
            server = smtplib.SMTP(settings.host, 587, timeout=20)
        try:
            server.set_debuglevel(0)
            server.ehlo()
            if settings.port == 587:
                server.starttls(context=context)
                server.ehlo()
            server.login(settings.user, settings.password)
            refused = server.send_message(message, from_addr=MAILBOX, to_addrs=[mail.to])
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
        finally:
            # A failed QUIT after a successful DATA acknowledgement is not a failed send.
            server.close()
    except Exception as exc:
        raise classify_smtp_error(exc) from None
