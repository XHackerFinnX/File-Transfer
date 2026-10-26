"""Signed server-to-server API, separate from the order webhook router."""

import asyncio
import logging
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from services.apex_email import EmailServiceNotConfigured, get_service
from services.apex_email_contract import MAX_BODY, EmailRequestError, parse_mail, verify_signature
from services.apex_email_queue import log_state

router = APIRouter()


def _one_header(request, name):
    values = request.headers.getlist(name)
    return values[0] if len(values) == 1 else ""


async def _read_raw(request):
    content_type = _one_header(request, "content-type")
    if not re.fullmatch(r'application/json(?:\s*;\s*charset\s*=\s*(?:utf-8|"utf-8"))?\s*', content_type, re.I):
        raise EmailRequestError(415, "unsupported_content_type")
    if request.headers.get("content-encoding", "identity").lower() != "identity":
        raise EmailRequestError(415, "unsupported_content_encoding")
    lengths = request.headers.getlist("content-length")
    if lengths:
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,20}", lengths[0]):
            raise EmailRequestError(400, "invalid_content_length")
        if int(lengths[0]) > MAX_BODY:
            raise EmailRequestError(413, "body_too_large")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY:
            raise EmailRequestError(413, "body_too_large")
        body.extend(chunk)
    return bytes(body)


def _error(status, code):
    return JSONResponse({"ok": False, "error": code}, status_code=status,
                        headers={"Cache-Control": "no-store", **({"Retry-After": "60"} if status == 503 else {})})


@router.post("/api/apex/email", include_in_schema=False)
async def accept_apex_email(request: Request):
    try:
        service = get_service()
    except EmailServiceNotConfigured:
        return _error(503, "email_service_not_configured")
    try:
        raw = await _read_raw(request)
        verify_signature(service.secret, _one_header(request, "x-apex-timestamp"),
                         _one_header(request, "x-apex-signature"), raw)
        mail = parse_mail(raw)
        if _one_header(request, "idempotency-key") != "apex-mail-" + mail.message_id:
            raise EmailRequestError(422, "invalid_idempotency_key")
        created, state = await asyncio.to_thread(service.queue.enqueue, mail, raw)
    except EmailRequestError as exc:
        return _error(exc.status, exc.code)
    except ClientDisconnect:
        return _error(400, "incomplete_body")
    except Exception:
        # Fail closed, including commit/disk failures. Do not log exception text or inputs.
        log_state("accept_failed", error="queue_unavailable", level=logging.ERROR)
        return _error(503, "queue_unavailable")
    service.queue.wake()
    response = {"ok": True, "message_id": mail.message_id}
    response.update({"state": state} if created else {"duplicate": True})
    return JSONResponse(response, status_code=202 if created else 200, headers={"Cache-Control": "no-store"})
