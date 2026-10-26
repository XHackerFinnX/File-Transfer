"""APEX v1 validation; errors never contain submitted values."""

import hashlib
import hmac
import json
import re
import time
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator, model_validator

MAX_BODY = 128 * 1024


class EmailRequestError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code
        super().__init__(code)


def verify_signature(secret: str, timestamp: str, signature: str, raw: bytes, now=None):
    if not re.fullmatch(r"[0-9]{1,12}", timestamp) or not re.fullmatch(r"sha256=[0-9a-f]{64}", signature):
        raise EmailRequestError(401, "invalid_signature")
    if abs((time.time() if now is None else now) - int(timestamp)) > 300:
        raise EmailRequestError(401, "invalid_timestamp")
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + raw,
                                    hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise EmailRequestError(401, "invalid_signature")


class MailData(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(default="", max_length=120)
    code: str | None = None
    reset_url: str | None = Field(default=None, max_length=4096)


class RequestContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ip: str | None = Field(default=None, max_length=100)
    user_agent: str | None = Field(default=None, max_length=2048)
    requested_at: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=500)


class ApexMail(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: StrictInt
    message_id: str
    source: Literal["apex"]
    site_url: Literal["https://apextop.ru"]
    kind: Literal["registration", "password_reset"]
    to: str = Field(min_length=3, max_length=254)
    subject: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=64 * 1024)
    html: str = Field(min_length=1, max_length=96 * 1024)
    created_at: datetime
    expires_at: datetime
    data: MailData
    request_context: RequestContext = Field(default_factory=RequestContext)

    @field_validator("schema_version")
    @classmethod
    def version(cls, value):
        if value != 1:
            raise ValueError("schema_version")
        return value

    @field_validator("message_id")
    @classmethod
    def identifier(cls, value):
        if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value):
            raise ValueError("message_id")
        UUID(value)
        return value

    @field_validator("to")
    @classmethod
    def recipient(cls, value):
        if any(c.isspace() or ord(c) < 32 or c in '<>,;():"' for c in value):
            raise ValueError("recipient")
        try:
            # Syntax only: never perform DNS requests using untrusted recipients.
            validate_email(value, check_deliverability=False, allow_smtputf8=False, test_environment=True)
        except EmailNotValidError:
            raise ValueError("recipient") from None
        if not value.isascii():
            raise ValueError("recipient")
        return value

    @field_validator("subject")
    @classmethod
    def safe_subject(cls, value):
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("subject")
        return value

    @field_validator("text", "html")
    @classmethod
    def content(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("content")
        return value

    @field_validator("created_at", "expires_at", mode="before")
    @classmethod
    def timestamp(cls, value):
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError("timestamp")
        value = datetime.fromisoformat(value)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone")
        return value

    @model_validator(mode="after")
    def semantics(self):
        ttl = (self.expires_at - self.created_at).total_seconds()
        if not 0 < ttl <= (600 if self.kind == "registration" else 1800) + 60:
            raise ValueError("ttl")
        if self.kind == "registration":
            if self.data.code is None or not re.fullmatch(r"[0-9]{6}", self.data.code) or self.data.reset_url is not None:
                raise ValueError("registration")
        else:
            if self.data.code is not None or self.data.reset_url is None:
                raise ValueError("password_reset")
            url = self.data.reset_url
            parts = urlsplit(url)
            if (not url.startswith("https://apextop.ru/account#")
                    or parts.scheme != "https" or parts.netloc != "apextop.ru"
                    or parts.path != "/account" or parts.query
                    or not re.fullmatch(r"reset-password=[A-Za-z0-9._~-]+", parts.fragment)):
                raise ValueError("reset_url")
        return self

    @property
    def canonical_id(self):
        return str(UUID(self.message_id))


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("invalid_constant")


def parse_mail(raw: bytes, now=None) -> ApexMail:
    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_keys, parse_constant=_invalid_constant)
        mail = ApexMail.model_validate(parsed)
        # Reject lone surrogates as well as malformed UTF-8; MIME must be encodable.
        for value in (mail.text, mail.html, mail.subject, mail.data.name):
            value.encode("utf-8")
        if mail.created_at.timestamp() > (time.time() if now is None else now) + 60:
            raise ValueError("future_created_at")
        return mail
    except (ValueError, TypeError, OverflowError, RecursionError, ValidationError):
        raise EmailRequestError(422, "invalid_payload") from None
