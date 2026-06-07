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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request,  UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

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
        raise HTTPException(status_code=400, detail="Можно прикреплять только png, jpg, jpeg и webp")
    return ext


@router.post("/api/launcher_feedback")
async def create_launcher_feedback(
    category: Annotated[str, Form()],
    subject: Annotated[str, Form()],
    description: Annotated[str, Form()],
    contact: Annotated[str, Form()] = "",
    include_technical_info: Annotated[bool, Form()] = False,
    technical_info: Annotated[str, Form()] = "{}",
    images: Annotated[list[UploadFile] | None, File()] = None,
):
    images = images or []
    category = category.strip()
    subject = subject.strip()
    description = description.strip()
    contact = contact.strip()[:120]

    if category not in ALLOWED_CATEGORIES:
        category = "Другое"
    if not 3 <= len(subject) <= 120:
        raise HTTPException(status_code=400, detail="Тема должна быть от 3 до 120 символов")
    if not 10 <= len(description) <= MAX_DESCRIPTION_LENGTH:
        raise HTTPException(status_code=400, detail="Описание должно быть от 10 до 5000 символов")
    if len(images) > MAX_FILES:
        raise HTTPException(status_code=400, detail="Можно прикрепить не больше 5 изображений")

    parsed_technical_info = {}
    if include_technical_info and technical_info:
        try:
            parsed_technical_info = json.loads(technical_info)
        except json.JSONDecodeError:
            parsed_technical_info = {"raw": technical_info[:4000]}

    created_at = datetime.now(timezone.utc)
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
            raise HTTPException(status_code=400, detail=f"{upload.filename}: максимум 5 МБ на файл")
        if total_bytes > MAX_TOTAL_BYTES:
            raise HTTPException(status_code=400, detail="Максимальный размер обращения — 20 МБ")
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
        "technical_info": parsed_technical_info,
        "images": saved_images,
        "status": "new",
    }
    (item_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "feedback_id": feedback_id, "message": "Обращение отправлено"}


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

    rows = "".join(
        f"""<tr>
            <td><span class="pill">{_esc(item.get('status', 'new'))}</span></td>
            <td><a href="/launcher_feedback/{_esc(item['id'])}">{_esc(item.get('subject', ''))}</a><br><span class="muted">{_esc(item.get('category', ''))}</span></td>
            <td>{_esc(item.get('contact')) if item.get('contact') else '<span class="muted">не указан</span>'}</td>
            <td class="muted">{_esc(item.get('created_at', ''))}</td>
            <td>{len(item.get('images') or [])}</td>
        </tr>"""
        for item in _list_feedback()
    ) or '<tr><td colspan="5" class="muted">Обращений пока нет.</td></tr>'

    return HTMLResponse(
        _html_page(
            "Обращения SLauncher",
            f"""
            <h1>Обращения SLauncher</h1>
            <p class="muted">Локальное хранение: {_esc(STORAGE_ROOT)}</p>
            <section class="card"><table>
                <thead><tr><th>Статус</th><th>Тема</th><th>Контакт</th><th>Дата</th><th>Фото</th></tr></thead>
                <tbody>{rows}</tbody>
            </table></section>
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
    return HTMLResponse(
        _html_page(
            _esc(item.get("subject", "Обращение")),
            f"""
            <p><a href="/launcher_feedback">← Назад к списку</a></p>
            <section class="card">
                <h1>{_esc(item.get('subject', ''))}</h1>
                <p><span class="pill">{_esc(item.get('category', ''))}</span> <span class="muted">{_esc(item.get('created_at', ''))}</span></p>
                <h3>Описание</h3><pre>{_esc(item.get('description', ''))}</pre>
                <h3>Контакт</h3><p>{_esc(item.get('contact')) if item.get('contact') else '<span class="muted">не указан</span>'}</p>
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