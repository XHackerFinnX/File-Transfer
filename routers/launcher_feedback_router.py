"""FastAPI router for SLauncher feedback.

Usage in your backend app:
    from backend.launcher_feedback_router import router as launcher_feedback_router
    app.include_router(launcher_feedback_router)

Environment variables:
    LAUNCHER_FEEDBACK_LOGIN=admin
    LAUNCHER_FEEDBACK_PASSWORD=change-me
    LAUNCHER_FEEDBACK_STORAGE=/var/www/launcher_feedback
"""

import html
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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

router = APIRouter()

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


def _html_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="ru">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <style>
        :root {{ color-scheme: dark; font-family: Inter, Arial, sans-serif; }}
        body {{ margin: 0; background: #0e1018; color: #e6e8f0; }}
        a {{ color: #ffb86c; text-decoration: none; }}
        .wrap {{ max-width: 1120px; margin: 0 auto; padding: 32px 18px; }}
        .card {{ background: #161826; border: 1px solid rgba(255,255,255,.1); border-radius: 16px; padding: 20px; box-shadow: 0 14px 45px rgba(0,0,0,.35); }}
        input, button, select {{ width: 100%; box-sizing: border-box; border-radius: 10px; border: 1px solid rgba(255,255,255,.12); padding: 12px 14px; background: #11131e; color: #e6e8f0; }}
        button {{ cursor: pointer; border: 0; background: linear-gradient(135deg, #ffb86c, #ff9a3c); color: #1a1c28; font-weight: 800; }}
        label {{ display: block; color: #8b90a8; font-size: 12px; font-weight: 700; text-transform: uppercase; margin: 14px 0 7px; }}
        table {{ width: 100%; border-collapse: collapse; overflow: hidden; }}
        th, td {{ padding: 12px; border-bottom: 1px solid rgba(255,255,255,.08); vertical-align: top; text-align: left; }}
        th {{ color: #8b90a8; font-size: 12px; text-transform: uppercase; }}
        .muted {{ color: #8b90a8; }}
        .pill {{ display: inline-flex; padding: 5px 9px; border-radius: 999px; background: rgba(255,184,108,.12); color: #ffb86c; font-size: 12px; font-weight: 700; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; }}
        .grid img {{ width: 100%; border-radius: 12px; border: 1px solid rgba(255,255,255,.1); }}
        pre {{ white-space: pre-wrap; background: #11131e; padding: 14px; border-radius: 12px; border: 1px solid rgba(255,255,255,.08); overflow:auto; }}
    </style>
</head>
<body><main class="wrap">{body}</main></body>
</html>"""


def _esc(value) -> str:
    return html.escape(str(value or ""), quote=True)


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
    record["blocked_until"] = _now_utc().isoformat()
    record["unblocked_at"] = _now_utc().isoformat()
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


def _feedback_dir(feedback_id: str) -> Path:
    safe_id = Path(feedback_id).name
    if safe_id != feedback_id:
        raise HTTPException(status_code=400, detail="Invalid feedback id")
    return FEEDBACK_ROOT / safe_id


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
            items.append(json.loads(meta_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(items, key=lambda item: item.get("created_at", ""), reverse=True)


def _image_ext(upload: UploadFile) -> str:
    content_type = (upload.content_type or "").lower()
    ext = ALLOWED_TYPES.get(content_type)
    if not ext:
        raise _api_error(400, "Можно прикреплять только png, jpg, jpeg и webp")
    return ext


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


@router.get("/launcher_feedback", response_class=HTMLResponse)
async def launcher_feedback_admin(request: Request):
    if not _is_authorized(request):
        return HTMLResponse(
            _html_page(
                "Вход — обращения SLauncher",
                """
                <section class="card" style="max-width:420px;margin:10vh auto 0;">
                    <h1>Помощь и отзывы</h1>
                    <p class="muted">Войдите, чтобы просмотреть обращения из лаунчера.</p>
                    <form method="post" action="/launcher_feedback/login">
                        <label>Логин</label><input name="login" autocomplete="username" required />
                        <label>Пароль</label><input name="password" type="password" autocomplete="current-password" required />
                        <div style="height:16px"></div><button type="submit">Войти</button>
                    </form>
                </section>
                """,
            )
        )

    status_filter = str(request.query_params.get("status") or "all")
    favorite_filter = str(request.query_params.get("favorite") or "all")
    date_from = str(request.query_params.get("date_from") or "")
    date_to = str(request.query_params.get("date_to") or "")
    from_dt = _parse_filter_date(date_from)
    to_dt = _parse_filter_date(date_to, end_of_day=True)

    items = []
    for item in _list_feedback():
        created_at = _parse_iso_datetime(str(item.get("created_at") or ""))
        if status_filter != "all" and item.get("status", "new") != status_filter:
            continue
        is_favorite = bool(item.get("favorite"))
        if favorite_filter == "yes" and not is_favorite:
            continue
        if favorite_filter == "no" and is_favorite:
            continue
        if from_dt and created_at and created_at < from_dt:
            continue
        if to_dt and created_at and created_at > to_dt:
            continue
        items.append(item)

    def selected(value: str, current: str) -> str:
        return " selected" if value == current else ""

    status_options = "".join(
        f'<option value="{status}"{selected(status, status_filter)}>{label}</option>'
        for status, label in [
            ("all", "Все статусы"),
            ("new", "Новое"),
            ("in_progress", "В работе"),
            ("closed", "Закрыто"),
        ]
    )
    favorite_options = "".join(
        f'<option value="{value}"{selected(value, favorite_filter)}>{label}</option>'
        for value, label in [("all", "Все"), ("yes", "Только избранные"), ("no", "Без избранного")]
    )

    rows = "".join(
        f"""<tr>
            <td><input type="checkbox" name="ids" value="{_esc(item['id'])}" /></td>
            <td>{'★' if item.get('favorite') else '☆'}</td>
            <td><span class="pill">{_esc(item.get('status', 'new'))}</span></td>
            <td><a href="/launcher_feedback/{_esc(item['id'])}">{_esc(item.get('subject', ''))}</a><br><span class="muted">{_esc(item.get('category', ''))}</span></td>
            <td>{_esc(item.get('contact')) if item.get('contact') else '<span class="muted">не указан</span>'}<br><span class="muted">ID: {_esc(item.get('system_id', ''))}</span></td>
            <td class="muted">{_esc(item.get('created_at', ''))}</td>
            <td>{len(item.get('images') or [])}</td>
        </tr>"""
        for item in items
    ) or '<tr><td colspan="7" class="muted">Обращений по выбранным фильтрам нет.</td></tr>'

    return HTMLResponse(
        _html_page(
            "Обращения SLauncher",
            f"""
            <h1>Обращения SLauncher</h1>
            <p class="muted">Локальное хранение: {_esc(STORAGE_ROOT)} · найдено: {len(items)}</p>
            <p><a href="/launcher_feedback/blocked">Список блокировок пользователей</a></p>

            <section class="card" style="margin-bottom:16px;">
                <form method="get" action="/launcher_feedback" style="display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;align-items:end;">
                    <div><label>Статус</label><select name="status">{status_options}</select></div>
                    <div><label>Избранное</label><select name="favorite">{favorite_options}</select></div>
                    <div><label>Дата от</label><input type="date" name="date_from" value="{_esc(date_from)}" /></div>
                    <div><label>Дата до</label><input type="date" name="date_to" value="{_esc(date_to)}" /></div>
                    <button type="submit">Фильтровать</button>
                </form>
            </section>

            <section class="card">
                <form method="post" action="/launcher_feedback/actions">
                    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px;">
                        <button type="submit" name="action" value="favorite">В избранное</button>
                        <button type="submit" name="action" value="unfavorite">Убрать из избранного</button>
                        <select name="status" style="max-width:170px;">
                            <option value="new">Новое</option>
                            <option value="in_progress">В работе</option>
                            <option value="closed">Закрыто</option>
                        </select>
                        <button type="submit" name="action" value="set_status">Сменить статус</button>
                        <button type="submit" name="action" value="delete" style="background:#f87171;color:#1a1c28;">Удалить выбранные</button>
                    </div>
                    <table>
                        <thead><tr><th></th><th>★</th><th>Статус</th><th>Тема</th><th>Контакт / ID</th><th>Дата</th><th>Фото</th></tr></thead>
                        <tbody>{rows}</tbody>
                    </table>
                </form>
            </section>
            """,
        )
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
    redirect = RedirectResponse("/launcher_feedback", status_code=303)
    redirect.set_cookie(SESSION_COOKIE, SESSION_TOKEN, httponly=True, secure=True, samesite="lax")
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


@router.get("/launcher_feedback/blocked", response_class=HTMLResponse)
async def launcher_feedback_blocked(request: Request):
    _require_auth(request)
    now = _now_utc()
    records = sorted(
        _load_blocklist().values(),
        key=lambda item: str(item.get("blocked_until") or ""),
        reverse=True,
    )
    rows = "".join(
        f"""<tr>
            <td>{_esc(record.get('system_id', ''))}</td>
            <td><span class="pill">{'Активна' if (_parse_iso_datetime(str(record.get('blocked_until') or '')) or now) > now else 'Истекла'}</span></td>
            <td class="muted">{_esc(record.get('blocked_until', ''))}</td>
            <td>{_esc(record.get('reason', ''))}</td>
            <td>
                <form method="post" action="/launcher_feedback/blocked/action" style="display:flex;gap:8px;">
                    <input type="hidden" name="system_id" value="{_esc(record.get('system_id', ''))}" />
                    <button type="submit" name="action" value="unblock">Разблокировать</button>
                    <button type="submit" name="action" value="block">Заблокировать на 1 час</button>
                </form>
            </td>
        </tr>"""
        for record in records
    ) or '<tr><td colspan="5" class="muted">Список блокировок пуст.</td></tr>'

    return HTMLResponse(
        _html_page(
            "Блокировки SLauncher",
            f"""
            <p><a href="/launcher_feedback">← Назад к обращениям</a></p>
            <h1>Блокировки пользователей</h1>
            <section class="card" style="margin-bottom:16px;">
                <form method="post" action="/launcher_feedback/blocked/action" style="display:grid;grid-template-columns:2fr 1fr 2fr auto;gap:12px;align-items:end;">
                    <div><label>System ID</label><input name="system_id" required placeholder="ID системы пользователя" /></div>
                    <div><label>Минут</label><input name="minutes" type="number" min="1" value="60" /></div>
                    <div><label>Причина</label><input name="reason" value="manual" /></div>
                    <button type="submit" name="action" value="block">Заблокировать</button>
                </form>
            </section>
            <section class="card"><table>
                <thead><tr><th>System ID</th><th>Статус</th><th>До</th><th>Причина</th><th>Действия</th></tr></thead>
                <tbody>{rows}</tbody>
            </table></section>
            """,
        )
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
    return RedirectResponse(f"/launcher_feedback/{_esc(feedback_id)}", status_code=303)


@router.get("/launcher_feedback/{feedback_id}", response_class=HTMLResponse)
async def launcher_feedback_item(request: Request, feedback_id: str):
    _require_auth(request)
    item = _read_meta(feedback_id)
    image_links = []
    for image in item.get("images") or []:
        safe_name = Path(image).name
        image_url = f"/launcher_feedback/{_esc(feedback_id)}/files/{_esc(safe_name)}"
        image_links.append(f'<a href="{image_url}" target="_blank"><img src="{image_url}" alt="image" /></a>')
    images = "".join(image_links) or '<p class="muted">Изображений нет.</p>'
    tech = _esc(json.dumps(item.get("technical_info") or {}, ensure_ascii=False, indent=2))
    current_status = str(item.get("status") or "new")
    status_options = "".join(
        f'<option value="{status}"{" selected" if status == current_status else ""}>{label}</option>'
        for status, label in [("new", "Новое"), ("in_progress", "В работе"), ("closed", "Закрыто")]
    )
    favorite_text = "Убрать из избранного" if item.get("favorite") else "В избранное"
    return HTMLResponse(
        _html_page(
            _esc(item.get("subject", "Обращение")),
            f"""
            <p><a href="/launcher_feedback">← Назад к списку</a></p>
            <section class="card">
                <h1>{'★ ' if item.get('favorite') else ''}{_esc(item.get('subject', ''))}</h1>
                <p><span class="pill">{_esc(item.get('category', ''))}</span> <span class="pill">{_esc(current_status)}</span> <span class="muted">{_esc(item.get('created_at', ''))}</span></p>
                <form method="post" action="/launcher_feedback/{_esc(feedback_id)}/action" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0;">
                    <button type="submit" name="action" value="toggle_favorite">{favorite_text}</button>
                    <select name="status" style="max-width:170px;">{status_options}</select>
                    <button type="submit" name="action" value="set_status">Сохранить статус</button>
                    <button type="submit" name="action" value="block_system">Заблокировать ID на 1 час</button>
                    <button type="submit" name="action" value="delete" style="background:#f87171;color:#1a1c28;">Удалить обращение</button>
                </form>
                <h3>Описание</h3><pre>{_esc(item.get('description', ''))}</pre>
                <h3>Контакт</h3><p>{_esc(item.get('contact')) if item.get('contact') else '<span class="muted">не указан</span>'}</p>
                <h3>System ID</h3><pre>{_esc(item.get('system_id', ''))}</pre>
                <h3>Изображения</h3><div class="grid">{images}</div>
                <h3>Техническая информация</h3><pre>{tech}</pre>
            </section>
            """,
        )
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