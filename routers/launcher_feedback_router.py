"""FastAPI router for SLauncher feedback.

Refactored version:
- Python logic is in this file.
- HTML is in ./templates.
- CSS is in ./static/launcher_feedback.css.

Usage in your backend app:
    from launcher_feedback_refactored.launcher_feedback_router import router as launcher_feedback_router
    app.include_router(launcher_feedback_router)

Environment variables:
    LAUNCHER_FEEDBACK_LOGIN=admin
    LAUNCHER_FEEDBACK_PASSWORD=change-me
    LAUNCHER_FEEDBACK_STORAGE=/var/www/launcher_feedback
    LAUNCHER_FEEDBACK_COOKIE_SECURE=false
"""

import json
import os
import secrets
import shutil
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODULE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static" / "css" / "launcher"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

STORAGE_ROOT = Path(os.getenv("LAUNCHER_FEEDBACK_STORAGE", "./launcher_feedback_data"))
FEEDBACK_ROOT = STORAGE_ROOT / "feedback"
SESSION_COOKIE = "launcher_feedback_session"
SESSION_TOKEN = secrets.token_urlsafe(32)

ALLOWED_CATEGORIES = {
    "Ошибка",
    "Предложение",
    "Вопрос",
    "Проблема с аккаунтом",
    "Проблема со сборкой",
    "Другое",
}
ALLOWED_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
MAX_FILES = 5
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_DESCRIPTION_LENGTH = 5000
FEEDBACK_STATUSES = {"new", "in_progress", "closed"}
BLOCKLIST_PATH = STORAGE_ROOT / "blocked_systems.json"
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 10
RATE_LIMIT_BLOCK_SECONDS = 60 * 60
_request_timestamps: dict[str, list[float]] = {}

STATUS_LABELS = {
    "new": "Новое",
    "in_progress": "В работе",
    "closed": "Закрыто",
}
STATUS_TONES = {
    "new": "blue",
    "in_progress": "orange",
    "closed": "green",
}


def _api_cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Cache-Control, Pragma, Accept",
        "Access-Control-Max-Age": "86400",
        "Vary": "Origin",
    }


def _api_error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=detail,
        headers=_api_cors_headers(),
    )


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _format_datetime(value: str) -> str:
    parsed = _parse_iso_datetime(str(value or ""))
    if not parsed:
        return str(value or "")
    return parsed.strftime("%d.%m.%Y %H:%M")


def _parse_filter_date(value: str, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if end_of_day:
            parsed = parsed + timedelta(days=1) - timedelta(microseconds=1)
        return parsed
    except ValueError:
        return None


def _safe_system_id(value: str) -> str:
    value = str(value or "").strip()
    safe = "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_", "."})
    return safe[:120]


def _safe_feedback_id(feedback_id: str) -> str:
    safe_id = Path(str(feedback_id)).name
    if safe_id != feedback_id:
        raise HTTPException(status_code=400, detail="Invalid feedback id")
    return safe_id


def _image_filename(image_path: str) -> str:
    return Path(str(image_path)).name


def _json_pretty(value) -> str:
    return json.dumps(value or {}, ensure_ascii=False, indent=2)


def _status_label(status: str) -> str:
    return STATUS_LABELS.get(str(status or "new"), str(status or "new"))


def _status_tone(status: str) -> str:
    return STATUS_TONES.get(str(status or "new"), "gray")


def _is_block_active(blocked_until: str) -> bool:
    parsed = _parse_iso_datetime(str(blocked_until or ""))
    return bool(parsed and parsed > _now_utc())


templates.env.filters["datetime_short"] = _format_datetime
templates.env.filters["image_filename"] = _image_filename
templates.env.filters["json_pretty"] = _json_pretty
templates.env.filters["status_label"] = _status_label
templates.env.filters["status_tone"] = _status_tone
templates.env.globals["status_labels"] = STATUS_LABELS
templates.env.globals["status_tones"] = STATUS_TONES
templates.env.globals["is_block_active"] = _is_block_active


def _load_blocklist() -> dict[str, dict]:
    if not BLOCKLIST_PATH.exists():
        return {}
    try:
        data = json.loads(BLOCKLIST_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_blocklist(data: dict[str, dict]) -> None:
    BLOCKLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    BLOCKLIST_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _blocked_until(system_id: str) -> datetime | None:
    record = _load_blocklist().get(system_id) or {}
    blocked_until = _parse_iso_datetime(str(record.get("blocked_until") or ""))
    if blocked_until and blocked_until > _now_utc():
        return blocked_until
    return None


def _set_system_block(system_id: str, minutes: int = 60, reason: str = "manual") -> dict:
    system_id = _safe_system_id(system_id)
    if not system_id:
        raise HTTPException(status_code=400, detail="System id is required")
    now = _now_utc()
    blocked_until = now + timedelta(minutes=max(1, int(minutes or 60)))
    data = _load_blocklist()
    record = data.get(system_id) or {}
    record.update(
        {
            "system_id": system_id,
            "blocked_at": now.isoformat(),
            "blocked_until": blocked_until.isoformat(),
            "reason": str(reason or "manual")[:300],
            "unblocked_at": "",
        }
    )
    data[system_id] = record
    _save_blocklist(data)
    return record


def _unblock_system(system_id: str) -> None:
    system_id = _safe_system_id(system_id)
    data = _load_blocklist()
    record = data.get(system_id)
    if not record:
        return
    now = _now_utc().isoformat()
    record["blocked_until"] = now
    record["unblocked_at"] = now
    data[system_id] = record
    _save_blocklist(data)


def _register_feedback_attempt(system_id: str) -> None:
    now = time.time()
    timestamps = [ts for ts in _request_timestamps.get(system_id, []) if now - ts <= RATE_LIMIT_WINDOW_SECONDS]
    timestamps.append(now)
    _request_timestamps[system_id] = timestamps
    if len(timestamps) > RATE_LIMIT_MAX_REQUESTS:
        record = _set_system_block(
            system_id,
            minutes=RATE_LIMIT_BLOCK_SECONDS // 60,
            reason=f"rate_limit_{RATE_LIMIT_MAX_REQUESTS}_per_{RATE_LIMIT_WINDOW_SECONDS}s",
        )
        raise _api_error(
            429,
            f"Слишком много обращений. Пользователь заблокирован до {record['blocked_until']}",
        )


def _feedback_dir(feedback_id: str) -> Path:
    return FEEDBACK_ROOT / _safe_feedback_id(feedback_id)


def _write_meta(item: dict) -> None:
    feedback_id = str(item.get("id") or "")
    if not feedback_id:
        raise HTTPException(status_code=400, detail="Invalid feedback item")
    (_feedback_dir(feedback_id) / "meta.json").write_text(
        json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _delete_feedback(feedback_id: str) -> None:
    shutil.rmtree(_feedback_dir(feedback_id), ignore_errors=True)


def _redirect_to_feedback(query: str = "") -> RedirectResponse:
    suffix = f"?{query}" if query else ""
    return RedirectResponse(f"/launcher_feedback{suffix}", status_code=303)


def _is_authorized(request: Request) -> bool:
    return secrets.compare_digest(request.cookies.get(SESSION_COOKIE, ""), SESSION_TOKEN)


def _require_auth(request: Request) -> None:
    if not _is_authorized(request):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _read_meta(feedback_id: str) -> dict:
    path = _feedback_dir(feedback_id) / "meta.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Feedback not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _list_feedback() -> list[dict]:
    if not FEEDBACK_ROOT.exists():
        return []
    items = []
    for meta_path in FEEDBACK_ROOT.glob("*/meta.json"):
        try:
            item = json.loads(meta_path.read_text(encoding="utf-8"))
            item.setdefault("status", "new")
            item.setdefault("favorite", False)
            items.append(item)
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(items, key=lambda item: item.get("created_at", ""), reverse=True)


def _filter_feedback_items(
    *,
    status_filter: str,
    favorite_filter: str,
    date_from: str,
    date_to: str,
    query: str,
) -> list[dict]:
    from_dt = _parse_filter_date(date_from)
    to_dt = _parse_filter_date(date_to, end_of_day=True)
    query_lower = query.strip().lower()

    items = []
    for item in _list_feedback():
        created_at = _parse_iso_datetime(str(item.get("created_at") or ""))
        status = item.get("status", "new")
        is_favorite = bool(item.get("favorite"))

        if status_filter != "all" and status != status_filter:
            continue
        if favorite_filter == "yes" and not is_favorite:
            continue
        if favorite_filter == "no" and is_favorite:
            continue
        if from_dt and created_at and created_at < from_dt:
            continue
        if to_dt and created_at and created_at > to_dt:
            continue
        if query_lower:
            searchable = " ".join(
                str(item.get(key, ""))
                for key in ("subject", "description", "category", "contact", "system_id")
            ).lower()
            if query_lower not in searchable:
                continue
        items.append(item)
    return items


def _feedback_stats(items: list[dict]) -> dict[str, int]:
    all_items = _list_feedback()
    return {
        "filtered": len(items),
        "total": len(all_items),
        "new": sum(1 for item in all_items if item.get("status", "new") == "new"),
        "in_progress": sum(1 for item in all_items if item.get("status") == "in_progress"),
        "closed": sum(1 for item in all_items if item.get("status") == "closed"),
        "favorite": sum(1 for item in all_items if item.get("favorite")),
    }


def _image_ext(upload: UploadFile) -> str:
    content_type = (upload.content_type or "").lower()
    ext = ALLOWED_TYPES.get(content_type)
    if not ext:
        raise _api_error(400, "Можно прикреплять только png, jpg, jpeg и webp")
    return ext


@router.get("/launcher_feedback/assets/{filename}")
async def launcher_feedback_asset(filename: str):
    safe_filename = Path(filename).name
    if safe_filename != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    file_path = STATIC_DIR / safe_filename
    if not file_path.exists() or file_path.suffix not in {".css"}:
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(file_path)


@router.options("/api/launcher_feedback")
async def launcher_feedback_options():
    return Response(status_code=204, headers=_api_cors_headers())


@router.post("/api/launcher_feedback")
async def create_launcher_feedback(
    request: Request,
    category: Annotated[str, Form()],
    subject: Annotated[str, Form()],
    description: Annotated[str, Form()],
    contact: Annotated[str, Form()] = "",
    system_id: Annotated[str, Form()] = "",
    include_technical_info: Annotated[bool, Form()] = False,
    technical_info: Annotated[str, Form()] = "{}",
    images: Annotated[list[UploadFile] | None, File()] = None,
):
    images = images or []
    category = category.strip()
    subject = subject.strip()
    description = description.strip()
    contact = contact.strip()[:120]
    system_id = _safe_system_id(system_id) or f"ip-{request.client.host if request.client else 'unknown'}"

    blocked_until = _blocked_until(system_id)
    if blocked_until:
        raise _api_error(429, f"Отправка обращений временно заблокирована до {blocked_until.isoformat()}")
    _register_feedback_attempt(system_id)

    if category not in ALLOWED_CATEGORIES:
        category = "Другое"
    if not 3 <= len(subject) <= 120:
        raise _api_error(400, "Тема должна быть от 3 до 120 символов")
    if not 10 <= len(description) <= MAX_DESCRIPTION_LENGTH:
        raise _api_error(400, "Описание должно быть от 10 до 5000 символов")
    if len(images) > MAX_FILES:
        raise _api_error(400, "Можно прикрепить не больше 5 изображений")

    parsed_technical_info = {}
    if include_technical_info and technical_info:
        try:
            parsed_technical_info = json.loads(technical_info)
        except json.JSONDecodeError:
            parsed_technical_info = {"raw": technical_info[:4000]}
    if isinstance(parsed_technical_info, dict):
        parsed_technical_info.setdefault("system_id", system_id)

    created_at = _now_utc()
    feedback_id = f"{created_at.strftime('%Y-%m-%d_%H-%M-%S')}_{uuid.uuid4().hex[:8]}"
    item_dir = _feedback_dir(feedback_id)
    image_dir = item_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    saved_images = []
    total_bytes = 0
    for index, upload in enumerate(images, start=1):
        ext = _image_ext(upload)
        content = await upload.read()
        total_bytes += len(content)
        if len(content) > MAX_FILE_BYTES:
            raise _api_error(400, f"{upload.filename}: максимум 5 МБ на файл")
        if total_bytes > MAX_TOTAL_BYTES:
            raise _api_error(400, "Максимальный размер обращения — 20 МБ")
        filename = f"{index}{ext}"
        (image_dir / filename).write_bytes(content)
        saved_images.append(f"images/{filename}")

    meta = {
        "id": feedback_id,
        "created_at": created_at.isoformat(),
        "category": category,
        "subject": subject,
        "description": description,
        "contact": contact,
        "system_id": system_id,
        "technical_info": parsed_technical_info,
        "images": saved_images,
        "status": "new",
        "favorite": False,
    }
    (item_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return JSONResponse(
        {"ok": True, "feedback_id": feedback_id, "message": "Обращение отправлено"},
        headers=_api_cors_headers(),
    )


@router.get("/launcher_feedback")
async def launcher_feedback_admin(request: Request):
    if not _is_authorized(request):
        return templates.TemplateResponse(
            "launcher/login.html",
            {
                "request": request,
                "title": "Вход — обращения SLauncher",
            },
        )

    status_filter = str(request.query_params.get("status") or "all")
    favorite_filter = str(request.query_params.get("favorite") or "all")
    date_from = str(request.query_params.get("date_from") or "")
    date_to = str(request.query_params.get("date_to") or "")
    query = str(request.query_params.get("q") or "")

    items = _filter_feedback_items(
        status_filter=status_filter,
        favorite_filter=favorite_filter,
        date_from=date_from,
        date_to=date_to,
        query=query,
    )

    return templates.TemplateResponse(
        "launcher/list.html",
        {
            "request": request,
            "title": "Обращения SLauncher",
            "items": items,
            "stats": _feedback_stats(items),
            "storage_root": STORAGE_ROOT,
            "filters": {
                "status": status_filter,
                "favorite": favorite_filter,
                "date_from": date_from,
                "date_to": date_to,
                "q": query,
            },
        },
    )


@router.post("/launcher_feedback/login")
async def launcher_feedback_login(
    login: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    expected_login = os.getenv("LAUNCHER_FEEDBACK_LOGIN", "admin")
    expected_password = os.getenv("LAUNCHER_FEEDBACK_PASSWORD", "Gjhyj12345!")
    if not secrets.compare_digest(login, expected_login) or not secrets.compare_digest(password, expected_password):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    secure_cookie = os.getenv("LAUNCHER_FEEDBACK_COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"}
    redirect = RedirectResponse("/launcher_feedback", status_code=303)
    redirect.set_cookie(SESSION_COOKIE, SESSION_TOKEN, httponly=True, secure=secure_cookie, samesite="lax")
    return redirect


@router.post("/launcher_feedback/actions")
async def launcher_feedback_actions(request: Request):
    _require_auth(request)
    form = await request.form()
    action = str(form.get("action") or "")
    ids = [str(value) for value in form.getlist("ids") if str(value).strip()]
    status = str(form.get("status") or "new")

    if not ids:
        return _redirect_to_feedback()

    for feedback_id in ids:
        if action == "delete":
            _delete_feedback(feedback_id)
            continue

        item = _read_meta(feedback_id)
        if action == "favorite":
            item["favorite"] = True
        elif action == "unfavorite":
            item["favorite"] = False
        elif action == "set_status" and status in FEEDBACK_STATUSES:
            item["status"] = status
        _write_meta(item)

    return _redirect_to_feedback()


@router.get("/launcher_feedback/blocked")
async def launcher_feedback_blocked(request: Request):
    _require_auth(request)
    records = sorted(
        _load_blocklist().values(),
        key=lambda item: str(item.get("blocked_until") or ""),
        reverse=True,
    )
    return templates.TemplateResponse(
        "launcher/blocked.html",
        {
            "request": request,
            "title": "Блокировки SLauncher",
            "records": records,
        },
    )


@router.post("/launcher_feedback/blocked/action")
async def launcher_feedback_blocked_action(request: Request):
    _require_auth(request)
    form = await request.form()
    action = str(form.get("action") or "")
    system_id = _safe_system_id(str(form.get("system_id") or ""))
    if action == "unblock":
        _unblock_system(system_id)
    elif action == "block":
        try:
            minutes = int(str(form.get("minutes") or "60") or 60)
        except ValueError:
            minutes = 60
        reason = str(form.get("reason") or "manual")
        _set_system_block(system_id, minutes=minutes, reason=reason)
    return RedirectResponse("/launcher_feedback/blocked", status_code=303)


@router.post("/launcher_feedback/{feedback_id}/action")
async def launcher_feedback_item_action(request: Request, feedback_id: str):
    _require_auth(request)
    form = await request.form()
    action = str(form.get("action") or "")

    if action == "delete":
        _delete_feedback(feedback_id)
        return _redirect_to_feedback()

    item = _read_meta(feedback_id)
    if action == "toggle_favorite":
        item["favorite"] = not bool(item.get("favorite"))
    elif action == "set_status":
        status = str(form.get("status") or "")
        if status in FEEDBACK_STATUSES:
            item["status"] = status
    elif action == "block_system":
        system_id = _safe_system_id(str(item.get("system_id") or ""))
        if system_id:
            _set_system_block(system_id, minutes=60, reason=f"manual_from_feedback_{feedback_id}")
    _write_meta(item)
    return RedirectResponse(f"/launcher_feedback/{_safe_feedback_id(feedback_id)}", status_code=303)


@router.get("/launcher_feedback/{feedback_id}")
async def launcher_feedback_item(request: Request, feedback_id: str):
    _require_auth(request)
    item = _read_meta(feedback_id)
    item.setdefault("status", "new")
    item.setdefault("favorite", False)

    return templates.TemplateResponse(
        "launcher/detail.html",
        {
            "request": request,
            "title": item.get("subject") or "Обращение",
            "item": item,
            "feedback_id": feedback_id,
        },
    )


@router.get("/launcher_feedback/{feedback_id}/files/{filename}")
async def launcher_feedback_file(request: Request, feedback_id: str, filename: str):
    _require_auth(request)
    safe_filename = Path(filename).name
    if safe_filename != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    file_path = _feedback_dir(feedback_id) / "images" / safe_filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path)
