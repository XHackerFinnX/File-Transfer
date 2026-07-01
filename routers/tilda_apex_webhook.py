import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from config import config
from db.tilda_orders import read_tilda_submissions, save_tilda_submission

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_SUBMISSIONS_IN_LIST = 500
# TODO: move to environment/settings when the admin keys are finalized.
FORM_ACCESS_SECRETS = {"apex": "secret"}
TILDA_SITE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _site_name_or_404(name: str) -> str:
    normalized = name.strip().lower()
    if not TILDA_SITE_NAME_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=404, detail="Tilda form storage not found")
    return normalized


def _form_secret_or_403(name: str, request: Request) -> None:
    expected_secret = FORM_ACCESS_SECRETS.get(_site_name_or_404(name))
    provided_secret = (
        request.query_params.get("secret")
        or request.query_params.get("token")
        or request.query_params.get("key")
        or request.headers.get("x-tilda-form-secret")
        or ""
    )
    if not expected_secret or provided_secret != expected_secret:
        raise HTTPException(status_code=403, detail="Invalid Tilda form secret")


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        return str(value)


def _normalize_form_mapping(values: dict[str, list[str]]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, items in values.items():
        normalized[key] = items[0] if len(items) == 1 else items
    return normalized


def _submission_summary(submission: dict[str, Any]) -> dict[str, Any]:
    payload = submission.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    customer_name = (
        payload.get("Name")
        or payload.get("name")
        or payload.get("Имя")
        or payload.get("fio")
        or payload.get("ФИО")
        or "Без имени"
    )
    contact = (
        payload.get("Phone")
        or payload.get("phone")
        or payload.get("Телефон")
        or payload.get("Email")
        or payload.get("email")
        or payload.get("Почта")
        or ""
    )

    return {
        "id": submission.get("id", ""),
        "created_at": submission.get("created_at", ""),
        "payload_type": submission.get("payload_type", ""),
        "customer_name": str(customer_name),
        "contact": str(contact),
        "payload": payload,
        "cookies": submission.get("cookies", {}),
        "client": submission.get("client", {}),
        "headers": submission.get("headers", {}),
    }


async def _extract_payload(request: Request) -> tuple[dict[str, Any], str]:
    content_type = request.headers.get("content-type", "").lower()
    body = await request.body()

    if len(body) > MAX_BODY_BYTES:
        return {"_error": "payload_too_large", "size": len(body)}, "too_large"

    if "application/json" in content_type:
        try:
            parsed = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {"_raw": body.decode("utf-8", errors="replace")}, "invalid_json"
        if isinstance(parsed, dict):
            return {str(key): _json_safe(value) for key, value in parsed.items()}, "json"
        return {"value": _json_safe(parsed)}, "json"

    if "application/x-www-form-urlencoded" in content_type or not content_type:
        parsed = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        return _normalize_form_mapping(parsed), "form"

    try:
        form = await request.form()
    except Exception:
        return {"_raw": body.decode("utf-8", errors="replace")}, "raw"

    payload: dict[str, Any] = {}
    for key, value in form.multi_items():
        if key in payload:
            existing = payload[key]
            if not isinstance(existing, list):
                payload[key] = [existing]
            payload[key].append(str(value))
        else:
            payload[key] = str(value)
    return payload, "multipart"


def _is_tilda_test(payload: dict[str, Any]) -> bool:
    return str(payload.get("test", "")).lower() == "test"


async def _save_submission(
    name: str,
    request: Request,
    payload: dict[str, Any],
    payload_type: str,
) -> str:
    created_at = _now_utc()
    submission_id = f"{created_at.strftime('%Y-%m-%d_%H-%M-%S')}_{uuid.uuid4().hex[:8]}"

    meta = {
        "id": submission_id,
        "site": _site_name_or_404(name),
        "created_at": created_at.isoformat(),
        "payload_type": payload_type,
        "payload": payload,
        "cookies": dict(request.cookies),
        "client": {
            "host": request.client.host if request.client else None,
            "port": request.client.port if request.client else None,
        },
        "headers": {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in {"authorization"}
        },
    }
    await save_tilda_submission(config.TILDA_APEX_DATABASE_TARGET, meta)
    return submission_id


async def _read_submissions(name: str) -> list[dict[str, Any]]:
    submissions = await read_tilda_submissions(
        config.TILDA_APEX_DATABASE_TARGET,
        _site_name_or_404(name),
        MAX_SUBMISSIONS_IN_LIST,
    )
    return [_submission_summary(submission) for submission in submissions]


@router.options("/tilda/{name}/webhook")
async def tilda_webhook_options(name: str):
    _site_name_or_404(name)
    return Response(status_code=204)


@router.post("/tilda/{name}/webhook")
async def tilda_webhook(name: str, request: Request):
    site_name = _site_name_or_404(name)
    payload, payload_type = await _extract_payload(request)

    if payload_type == "too_large":
        return JSONResponse({"ok": False, "error": "payload_too_large"}, status_code=413)

    if _is_tilda_test(payload):
        return JSONResponse(
            {"ok": True, "site": site_name, "message": "Tilda webhook test received"}
        )

    submission_id = await _save_submission(site_name, request, payload, payload_type)
    return JSONResponse({"ok": True, "site": site_name, "submission_id": submission_id})


@router.get("/tilda/{name}/form", response_class=HTMLResponse)
async def tilda_form_page(name: str, request: Request):
    site_name = _site_name_or_404(name)
    _form_secret_or_403(site_name, request)
    return templates.TemplateResponse(
        request=request,
        name="tilda/form.html",
        context={"site_name": site_name},
    )


@router.get("/tilda/{name}/form/submissions")
async def tilda_form_submissions(name: str, request: Request):
    site_name = _site_name_or_404(name)
    _form_secret_or_403(site_name, request)
    return JSONResponse(
        {
            "ok": True,
            "site": site_name,
            "submissions": await _read_submissions(site_name),
        }
    )