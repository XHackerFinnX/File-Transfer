import asyncio
import hashlib
import html
import json
import logging
import re
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from config import config
from db.connections import close_database_pools, open_database_pools, recover_database_pool
from db.errors import DATABASE_ERRORS, database_error_code
from db.schemas import create_tilda_submissions_schema
from db.tilda_orders import (
    read_tilda_submissions,
    read_tilda_submission_identity,
    save_tilda_email_message,
    save_tilda_submission,
    update_tilda_submission_im_number,
)
from services.tilda_cdek import (
    cdek_credentials,
    cdek_get_token,
    find_cdek_order,
    send_customer_email,
    update_cdek_order_number,
)
from services.tilda_payload import (
    can_auto_update_cdek,
    decimal_value,
    first_value as _first_value,
    is_taplink_payload,
    normalize_order_payload,
    order_identity,
    order_number,
    payment_payload as _payment_payload,
    webhook_key,
)
from services.tilda_outbox import Delivery, LeaseLost, TildaOutbox
from services.tilda_sheets import GoogleSheetsBackup

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
MAX_BODY_BYTES = 2 * 1024 * 1024
TILDA_SITE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
FORM_ASSETS_VERSION = hashlib.sha256(b"".join(
    (PROJECT_ROOT / "static" / relative).read_bytes()
    for relative in ("js/tilda/form.js", "js/tilda/custom_filters.js",
                     "css/tilda/form.css", "css/tilda/custom_filters.css")
)).hexdigest()[:12]
PRIVATE_FORM_HEADERS = {"Cache-Control": "private, no-store"}


# ---------------------------------------------------------------------------
# Компактное логирование основных этапов Tilda webhook.
# Бизнес-данные заказа, email, телефон, headers и payload в журнал не выводятся.
# ---------------------------------------------------------------------------
WEBHOOK_LOGGER = logging.getLogger("tilda.webhook")
WEBHOOK_LOGGER.setLevel(logging.INFO)
WEBHOOK_LOGGER.propagate = False

if not WEBHOOK_LOGGER.handlers:
    _webhook_handler = logging.StreamHandler()
    _webhook_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    WEBHOOK_LOGGER.addHandler(_webhook_handler)


def _short_webhook_error(exc: Exception) -> str:
    """Короткое описание ошибки без traceback и длинного URL."""
    if isinstance(exc, DATABASE_ERRORS):
        return database_error_code(exc)
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    reason = str(getattr(response, "reason", "") or "").strip()

    if status_code:
        return f"HTTP {status_code}" + (f" {reason}" if reason else "")

    message = " ".join(str(exc).split())
    message = re.sub(r"https?://\S+", "[URL]", message)
    if len(message) > 180:
        message = message[:177] + "..."
    return message or type(exc).__name__


def _log_webhook(
    level: int,
    raw_site: str,
    trace_id: str,
    step: str,
    *,
    order_id: str = "",
    submission_id: str = "",
    error: str = "",
) -> None:
    """Одна короткая строка журнала на важный этап обработки webhook."""
    record: dict[str, Any] = {
        "raw_site": raw_site,
        "order_id": order_id,
        "submission_id": submission_id,
        "trace_id": trace_id,
        "шаг": step,
    }
    if error:
        record["ошибка"] = error

    WEBHOOK_LOGGER.log(
        level,
        json.dumps(record, ensure_ascii=False, default=str, separators=(",", ":")),
    )

class CdekImNumberUpdateRequest(BaseModel):
    current_im_number: str
    new_im_number: str
    order_id: str | None = None
    order_uuid: str | None = None
    submission_id: str | None = None


class TildaEmailSendRequest(BaseModel):
    submission_id: str
    message_type: str
    to_email: str
    customer_name: str | None = None
    order_id: str | None = None
    order_sum: str | None = None
    delivery_sum: str | None = None
    delivery_text: str | None = None
    custom_subject: str | None = None
    custom_body: str | None = None


class TildaSubmissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    site: str | None = None
    created_at: str = Field(default_factory=lambda: _now_utc().isoformat(), max_length=64)
    payload_type: str = Field(default="json", pattern=r"^(json|form|multipart|raw)$")
    payload: dict[str, Any]
    cookies: dict[str, Any] = Field(default_factory=dict)
    client: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, Any] = Field(default_factory=dict)
    im_number: str | None = Field(default=None, max_length=512)
    webhook_key: str | None = Field(default=None, max_length=1024)

    @field_validator("created_at")
    @classmethod
    def aware_timestamp(cls, value):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("created_at requires timezone")
        return parsed.isoformat()
    

def _project_config_maps() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Build project -> secret/database/CDEK maps from TILDA_PROJECTS_JSON.

    Example:
    {"new_shop":{"secret":"...","database_target":"apex","cdek_account":"shop_cdek"}}
    """
    if not config.TILDA_PROJECTS_JSON.strip():
        raise ValueError("TILDA_PROJECTS_JSON is required")

    try:
        projects = json.loads(config.TILDA_PROJECTS_JSON)
    except json.JSONDecodeError as exc:
        raise ValueError("TILDA_PROJECTS_JSON contains invalid JSON") from exc
    if not isinstance(projects, dict):
        raise ValueError("TILDA_PROJECTS_JSON must be a JSON object")

    secrets: dict[str, str] = {}
    database_targets: dict[str, str] = {}
    cdek_accounts: dict[str, str] = {}
    for raw_name, raw_project in projects.items():
        site_name = _site_name_or_404(str(raw_name))
        if not isinstance(raw_project, dict):
            raise ValueError(f"Tilda project {site_name!r} must be an object")
        secret = str(raw_project.get("secret") or "").strip()
        database_target = str(raw_project.get("database_target") or "").strip()
        cdek_account = str(raw_project.get("cdek_account") or "").strip()
        if not secret:
            raise ValueError(f"Tilda project {site_name!r} requires secret")
        if not database_target:
            raise ValueError(f"Tilda project {site_name!r} requires database_target")
        if not cdek_account:
            raise ValueError(f"Tilda project {site_name!r} requires cdek_account")
        secrets[site_name] = secret
        database_targets[site_name] = database_target
        cdek_accounts[site_name] = cdek_account

    return secrets, database_targets, cdek_accounts


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _site_name_or_404(name: str) -> str:
    normalized = name.strip().lower()
    if not TILDA_SITE_NAME_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=404, detail="Tilda form storage not found")
    return normalized


def _email_sender_name(name: str) -> str:
    site_name = _site_name_or_404(name)
    if site_name in {"apex", "apexf1"}:
        return "APEX"
    return site_name.replace("_", " ").replace("-", " ").upper()


def _database_target_or_404(name: str) -> str:
    site_name = _site_name_or_404(name)
    _, database_targets, _ = _project_config_maps()
    database_target = database_targets.get(site_name)
    if not database_target:
        raise HTTPException(status_code=404, detail="Tilda form storage not found")
    return database_target


def _cdek_account_or_404(name: str) -> str:
    site_name = _site_name_or_404(name)
    _, _, cdek_accounts = _project_config_maps()
    account_name = cdek_accounts.get(site_name)
    if not account_name:
        raise HTTPException(status_code=404, detail="CDEK account mapping not found")
    return account_name


def _form_secret_or_403(name: str, request: Request) -> None:
    secrets, _, _ = _project_config_maps()
    expected_secret = secrets.get(_site_name_or_404(name))
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
    order_payload = normalize_order_payload(payload)
    customer_email = str(_first_value(order_payload, ["Email", "email", "Почта"]) or "").strip().lower()

    customer_name = (
        order_payload.get("Name")
        or order_payload.get("name")
        or order_payload.get("Имя")
        or order_payload.get("fio")
        or order_payload.get("ФИО")
        or "Без имени"
    )
    contact = (
        order_payload.get("Phone")
        or order_payload.get("phone")
        or order_payload.get("Телефон")
        or order_payload.get("Email")
        or order_payload.get("email")
        or order_payload.get("Почта")
        or ""
    )

    im_number = str(submission.get("im_number") or "")
    if is_taplink_payload(payload) and im_number == order_identity(payload):
        # Older rows were initialized with the technical Taplink order_id.
        im_number = order_number(payload)

    summary = {
        "id": submission.get("id", ""),
        "created_at": submission.get("created_at", ""),
        "payload_type": submission.get("payload_type", ""),
        "customer_name": str(customer_name),
        "customer_email": customer_email,
        "contact": str(contact),
        "payload": payload,
        "cookies": submission.get("cookies", {}),
        "client": submission.get("client", {}),
        "headers": submission.get("headers", {}),
        "im_number": im_number,
        "email_messages": submission.get("email_messages") or [],
        "storage_status": submission.get("storage_status", "database"),
        "queue_attempts": submission.get("queue_attempts", 0),
        "queue_error": submission.get("queue_error", ""),
        "external_backup": submission.get("external_backup", {"status": "disabled"}),
    }
    if order_payload is not payload:
        summary.update({
            "order_payload": order_payload,
            "order_source": "taplink" if is_taplink_payload(payload) else "tilda",
            "external_order_id": order_identity(payload),
        })
    return summary


async def _extract_payload(request: Request) -> tuple[dict[str, Any], str]:
    content_type = request.headers.get("content-type", "").lower()
    body = await request.body()

    if len(body) > MAX_BODY_BYTES:
        return {"_error": "payload_too_large", "size": len(body)}, "too_large"

    decoded_body = body.decode("utf-8-sig", errors="replace")
    if "application/json" in content_type or decoded_body.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(decoded_body or "{}")
        except json.JSONDecodeError:
            if "application/json" in content_type:
                return {"_raw": decoded_body}, "invalid_json"
        else:
            if isinstance(parsed, dict):
                return {str(key): _json_safe(value) for key, value in parsed.items()}, "json"
            return {"value": _json_safe(parsed)}, "json"

    if "application/x-www-form-urlencoded" in content_type or not content_type:
        parsed = parse_qs(
            body.decode("utf-8", errors="replace"), keep_blank_values=True
        )
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
) -> tuple[str, bool]:
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
    outbox = _get_tilda_outbox()
    await outbox.initialize()
    return await outbox.enqueue(_database_target_or_404(name), meta)


async def _read_submissions(name: str) -> list[dict[str, Any]]:
    submissions, _, _ = await _read_submission_state(name)
    return submissions


async def _read_submission_state(name):
    target, site = _database_target_or_404(name), _site_name_or_404(name)
    outbox = _get_tilda_outbox()
    await outbox.initialize()
    available = outbox.blocked_until.get(target, 0) <= time.time()
    submissions = []
    if available:
        try:
            submissions = await read_tilda_submissions(target, site)
        except DATABASE_ERRORS as exc:
            available = False
            outbox.database_unavailable(target)
            _log_webhook(logging.WARNING, site, "", "БД недоступна: показываем локальную очередь",
                         error=_short_webhook_error(exc))
    pending = await outbox.pending_submissions(target, site)
    indexed = {webhook_key(row.get("payload", {})): row for row in submissions}
    for row in pending:
        key = webhook_key(row.get("payload", {}))
        if key in indexed:
            indexed[key] = {**indexed[key], "storage_status": "queued",
                            "queue_attempts": row["queue_attempts"], "queue_error": row["queue_error"],
                            "external_backup": row["external_backup"]}
        else:
            indexed[key] = row
    result = sorted(indexed.values(), key=lambda row: str(row.get("created_at", "")), reverse=True)
    return [_submission_summary(row) for row in result], available, len(pending)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _format_detail_value(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value).strip()


def _option_value(product: dict[str, Any], names: list[str]) -> str:
    direct = _first_value(product, names)
    if direct:
        return str(direct)
    normalized = {name.lower() for name in names}
    options = product.get("options")
    if not isinstance(options, list):
        return ""
    for item in options:
        if not isinstance(item, dict):
            continue
        option_name = str(item.get("option") or item.get("name") or "").lower()
        if option_name in normalized:
            return str(item.get("variant") or item.get("value") or "")
    return ""


def _order_details_from_submission(submission: dict[str, Any] | None) -> dict[str, Any]:
    order_payload = normalize_order_payload(_as_dict((submission or {}).get("payload")))
    payment = _payment_payload(order_payload)
    product_source = (
        payment.get("products")
        or order_payload.get("products")
        or order_payload.get("Products")
        or order_payload.get("Товары")
        or order_payload.get("items")
        or []
    )
    products: list[dict[str, str]] = []
    if isinstance(product_source, list):
        for product in product_source:
            if not isinstance(product, dict):
                continue
            products.append(
                {
                    "name": _format_detail_value(
                        _first_value(
                            product,
                            ["name", "title", "product_name", "Название", "Товар"],
                        )
                    ),
                    "sku": _format_detail_value(product.get("sku")),
                    "quantity": _format_detail_value(
                        _first_value(
                            product,
                            ["quantity", "count", "amount", "qty", "Количество"],
                        )
                        or 1
                    ),
                    "price": _format_detail_value(
                        _first_value(
                            product, ["price", "item_price", "Цена", "Стоимость"]
                        )
                    ),
                    "size": _format_detail_value(
                        _option_value(product, ["Размер", "size", "Size"])
                    ),
                    "color": _format_detail_value(
                        _option_value(product, ["Цвет", "color", "Color"])
                    ),
                }
            )

    return {
        "customer": {
            "name": _format_detail_value(
                _first_value(
                    order_payload, ["Name", "name", "Full name", "Имя", "ФИО", "fio"]
                )
            ),
            "phone": _format_detail_value(
                _first_value(order_payload, ["Phone", "phone", "Телефон"])
            ),
            "email": _format_detail_value(
                _first_value(order_payload, ["Email", "email", "Почта"])
            ),
        },
        "delivery": {
            "type": _format_detail_value(
                payment.get("delivery")
                or _first_value(order_payload, ["delivery", "Доставка"])
            ),
            "address": _format_detail_value(payment.get("delivery_address")),
            "city": _format_detail_value(payment.get("delivery_city")),
            "region": _format_detail_value(payment.get("delivery_region")),
            "zip": _format_detail_value(payment.get("delivery_zip")),
            "pickup_id": _format_detail_value(payment.get("delivery_pickup_id")),
            "fio": _format_detail_value(payment.get("delivery_fio")),
            "comment": _format_detail_value(payment.get("delivery_comment")),
        },
        "products": products,
    }


def _plain_detail_lines(title: str, values: dict[str, str]) -> list[str]:
    lines = [title]
    for label, value in values.items():
        if value:
            lines.append(f"- {label}: {value}")
    return lines if len(lines) > 1 else []


def _html_detail_row(label: str, value: str) -> str:
    if not value:
        return ""
    return (
        '<div style="padding:10px 12px;border-radius:12px;background:#f8fafc;border:1px solid #e5e7eb;">'
        f'<span style="color:#64748b;font-size:13px;">{html.escape(label)}</span><br>'
        f"<b>{html.escape(value)}</b></div>"
    )
    

def _build_order_email(
    payload: TildaEmailSendRequest,
    site_name: str,
    submission: dict[str, Any] | None = None,
) -> tuple[str, str, str]:
    if submission:
        defaults = _email_request_from_submission(
            payload.submission_id,
            {**_as_dict(submission.get("payload")), "Email": payload.to_email},
            site_name,
        )
        if defaults:
            payload = payload.model_copy(update={
                key: getattr(defaults, key)
                for key in ("customer_name", "order_id", "order_sum", "delivery_sum", "delivery_text")
                if not getattr(payload, key)
            })
    customer_name = (payload.customer_name or "покупатель").strip() or "покупатель"
    order_id = (payload.order_id or payload.submission_id).strip()
    order_sum = (payload.order_sum or "—").strip()
    delivery_sum = (payload.delivery_sum or "—").strip()
    delivery_text = (payload.delivery_text or "Доставка не указана").strip()
    shop_name = site_name.replace("_", " ").replace("-", " ").title()
    subject = f"Ваш заказ №{order_id} оформлен в магазине {shop_name}"
    details = _order_details_from_submission(submission)

    detail_lines = [
        *_plain_detail_lines(
            "Покупатель",
            {
                "Имя": details["customer"]["name"],
                "Телефон": details["customer"]["phone"],
                "Email": details["customer"]["email"],
            },
        ),
        *_plain_detail_lines(
            "Доставка",
            {
                "Способ": details["delivery"]["type"] or delivery_text,
                "ФИО получателя": details["delivery"]["fio"],
                "Город": details["delivery"]["city"],
                "Регион": details["delivery"]["region"],
                "Индекс": details["delivery"]["zip"],
                "Адрес": details["delivery"]["address"],
                "ПВЗ": details["delivery"]["pickup_id"],
                "Комментарий": details["delivery"]["comment"],
            },
        ),
    ]
    if details["products"]:
        detail_lines.append("Товары")
        for index, product in enumerate(details["products"], start=1):
            options = ", ".join(
                item
                for item in [
                    f"SKU: {product['sku']}" if product["sku"] else "",
                    f"цвет: {product['color']}" if product["color"] else "",
                    f"размер: {product['size']}" if product["size"] else "",
                    f"кол-во: {product['quantity']}" if product["quantity"] else "",
                    f"цена: {product['price']}" if product["price"] else "",
                ]
                if item
            )
            detail_lines.append(
                f"{index}. {product['name'] or 'Товар'}"
                + (f" ({options})" if options else "")
            )
    details_text = "\n".join(detail_lines)
    body = (
        f"Здравствуйте, {customer_name}!\n\n"
        f"Вы оформили заказ №{order_id} в магазине {shop_name}.\n"
        f"Сумма заказа: {order_sum}.\n"
        f"Доставка: {delivery_text}. Стоимость доставки: {delivery_sum}.\n\n"
        + (f"Подробности заказа:\n{details_text}\n\n" if details_text else "")
        + "Мы уже получили вашу заявку и скоро свяжемся с вами по деталям отправки."
    )
    safe_customer = html.escape(customer_name)
    safe_order = html.escape(order_id)
    safe_shop = html.escape(shop_name)
    safe_order_sum = html.escape(order_sum)
    safe_delivery_sum = html.escape(delivery_sum)
    safe_delivery_text = html.escape(delivery_text)
    customer_html = "".join(
        [
            _html_detail_row("Имя", details["customer"]["name"]),
            _html_detail_row("Телефон", details["customer"]["phone"]),
            _html_detail_row("Email", details["customer"]["email"]),
        ]
    )
    delivery_html = "".join(
        [
            _html_detail_row("Способ", details["delivery"]["type"] or delivery_text),
            _html_detail_row("ФИО получателя", details["delivery"]["fio"]),
            _html_detail_row("Город", details["delivery"]["city"]),
            _html_detail_row("Регион", details["delivery"]["region"]),
            _html_detail_row("Индекс", details["delivery"]["zip"]),
            _html_detail_row("Адрес", details["delivery"]["address"]),
            _html_detail_row("ПВЗ", details["delivery"]["pickup_id"]),
            _html_detail_row("Комментарий", details["delivery"]["comment"]),
        ]
    )
    products_html = "".join(f"""<tr>
            <td style="padding:10px;border-bottom:1px solid #e5e7eb;">{html.escape(product['name'] or 'Товар')}</td>
            <td style="padding:10px;border-bottom:1px solid #e5e7eb;">{html.escape(product['sku'] or '—')}</td>
            <td style="padding:10px;border-bottom:1px solid #e5e7eb;">{html.escape(product['color'] or '—')}</td>
            <td style="padding:10px;border-bottom:1px solid #e5e7eb;">{html.escape(product['size'] or '—')}</td>
            <td style="padding:10px;border-bottom:1px solid #e5e7eb;">{html.escape(product['quantity'] or '—')}</td>
            <td style="padding:10px;border-bottom:1px solid #e5e7eb;">{html.escape(product['price'] or '—')}</td>
        </tr>""" for product in details["products"])
    products_section = (
        f"""
          <h2 style="font-size:18px;margin:26px 0 12px;">Товары</h2>
          <table style="width:100%;border-collapse:collapse;font-size:14px;">
            <thead><tr style="background:#f1f5f9;color:#475569;text-align:left;"><th style="padding:10px;">Товар</th><th style="padding:10px;">SKU</th><th style="padding:10px;">Цвет</th><th style="padding:10px;">Размер</th><th style="padding:10px;">Кол-во</th><th style="padding:10px;">Цена</th></tr></thead>
            <tbody>{products_html}</tbody>
          </table>"""
        if products_html
        else ""
    )
    body_html = f"""
    <div style="margin:0;padding:24px;background:#f6f7fb;font-family:Arial,sans-serif;color:#14171f;">
      <div style="max-width:620px;margin:0 auto;background:#ffffff;border-radius:22px;overflow:hidden;border:1px solid #e7e9f0;">
        <div style="padding:28px;background:linear-gradient(135deg,#111827,#2f3a4f);color:#ffffff;">
          <div style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;opacity:.75;">{safe_shop}</div>
          <h1 style="margin:10px 0 0;font-size:26px;line-height:1.25;">Заказ успешно оформлен</h1>
        </div>
        <div style="padding:28px;">
          <p style="font-size:17px;line-height:1.6;margin:0 0 18px;">Здравствуйте, <b>{safe_customer}</b>!</p>
          <p style="font-size:16px;line-height:1.6;margin:0 0 22px;">Мы получили ваш заказ <b>№{safe_order}</b> и уже готовим его к обработке.</p>
          <div style="display:grid;gap:12px;margin:22px 0;">
            <div style="padding:14px 16px;border-radius:14px;background:#f8fafc;border:1px solid #e5e7eb;"><span style="color:#64748b;">Сумма заказа</span><br><b style="font-size:18px;">{safe_order_sum}</b></div>
            <div style="padding:14px 16px;border-radius:14px;background:#f8fafc;border:1px solid #e5e7eb;"><span style="color:#64748b;">Доставка</span><br><b>{safe_delivery_text}</b><br><span>{safe_delivery_sum}</span></div>
          </div>
          <h2 style="font-size:18px;margin:26px 0 12px;">Покупатель</h2>
          <div style="display:grid;gap:10px;margin:0 0 18px;">{customer_html}</div>
          <h2 style="font-size:18px;margin:26px 0 12px;">Доставка</h2>
          <div style="display:grid;gap:10px;margin:0 0 18px;">{delivery_html}</div>
          {products_section}
          <p style="font-size:15px;line-height:1.6;color:#475569;margin:24px 0 0;">Скоро мы пришлём дополнительную информацию по сборке и отправке заказа.</p>
        </div>
      </div>
    </div>
    """
    return subject, body, body_html


def _money_text(value: Any) -> str:
    if value in (None, ""):
        return "—"
    amount = decimal_value(value)
    if amount is None:
        return str(value)
    return f"{amount:,.2f}".replace(",", " ").replace(".", ",") + " RUB"


def _order_id_from_payload(payload: dict[str, Any]) -> str:
    return order_number(payload)


def _email_request_from_submission(
    submission_id: str, payload: dict[str, Any], site_name: str
) -> TildaEmailSendRequest | None:
    payload = normalize_order_payload(payload)
    payment = _payment_payload(payload)
    to_email = (
        str(_first_value(payload, ["Email", "email", "Почта"]) or "").strip().lower()
    )
    if not to_email or "@" not in to_email:
        return None
    return TildaEmailSendRequest(
        submission_id=submission_id,
        message_type="order_notification",
        to_email=to_email,
        customer_name=str(
            _first_value(payload, ["Name", "name", "Full name", "Имя", "ФИО", "fio"])
            or ""
        ),
        order_id=_order_id_from_payload(payload) or submission_id,
        order_sum=_money_text(
            _first_value(payment, ["amount"])
            if payment.get("amount") not in (None, "")
            else _first_value(payload, ["amount", "total", "sum", "Сумма"])
        ),
        delivery_sum=_money_text(
            _first_value(payment, ["delivery_price"])
            if payment.get("delivery_price") not in (None, "")
            else _first_value(payload, ["delivery_sum", "Стоимость доставки"])
        ),
        delivery_text=str(
            payment.get("delivery")
            or _first_value(payload, ["delivery", "Доставка"])
            or "Доставка не указана"
        ),
    )


async def _send_order_notification_for_submission(
    database_target: str, site_name: str, submission_id: str, payload: dict[str, Any],
    delivery: Delivery | None = None,
) -> dict[str, Any] | None:
    action = delivery.state.setdefault("email", {}) if delivery else {}
    if action.get("status") in {"failed", "unknown", "skipped"}:
        return None
    if action.get("status") == "saved":
        return action["message"]
    if action.get("status") == "sending":
        action["status"] = "unknown"
        await delivery.checkpoint()
        _log_webhook(logging.ERROR, site_name, submission_id,
                     "Исход SMTP после перезапуска неизвестен: автоматический повтор письма пропущен",
                     submission_id=submission_id)
        return None
    email_payload = _email_request_from_submission(submission_id, payload, site_name)
    if not email_payload:
        if delivery:
            action["status"] = "skipped"
            await delivery.checkpoint()
        return None
    if action.get("status") != "sent":
        submission = {"id": submission_id, "payload": payload}
        subject, body, body_html = _build_order_email(email_payload, site_name, submission)
        action["message"] = {
            "id": f"{_now_utc().strftime('%Y-%m-%d_%H-%M-%S')}_{uuid.uuid4().hex[:8]}",
            "submission_id": submission_id,
            "site": site_name,
            "created_at": _now_utc().isoformat(),
            "message_type": "order_notification",
            "to_email": email_payload.to_email,
            "subject": subject,
            "body": body,
            "body_html": body_html,
            "status": "sent",
        }
        if delivery:
            action["status"] = "sending"
            await delivery.checkpoint()
        try:
            await asyncio.to_thread(send_customer_email, email_payload.to_email, subject, body, body_html,
                                    sender_name=_email_sender_name(site_name))
        except Exception:
            if delivery:
                action["status"] = "failed"
                await delivery.checkpoint()
            raise
        if delivery:
            action["status"] = "sent"
            await delivery.checkpoint()
    message = await save_tilda_email_message(database_target, action["message"])
    if delivery:
        action["status"] = "saved"
        await delivery.checkpoint()
    return message


def _cdek_im_number_prefix(account_name: str) -> str:
    credentials = cdek_credentials(account_name)
    prefix = str(credentials.get("prefix_number") or "").strip()
    if not prefix:
        return ""
    return prefix if prefix.endswith("_") else f"{prefix}_"


async def _auto_update_cdek_im_number(
    database_target: str, site_name: str, submission_id: str, payload: dict[str, Any],
    delivery: Delivery | None = None,
) -> str | None:
    if not can_auto_update_cdek(payload):
        return None
    order_id = _order_id_from_payload(payload)
    if not order_id:
        return None
    action = delivery.state.setdefault("cdek", {}) if delivery else {}
    if action.get("status") == "failed":
        return None
    if action.get("status") == "saved":
        return action["number"]
    if action.get("status") != "updated":
        cdek_account = _cdek_account_or_404(site_name)
        new_im_number = action.get("number") or f"{_cdek_im_number_prefix(cdek_account)}{order_id}"
        if new_im_number == order_id:
            return None
        token = await asyncio.to_thread(cdek_get_token, cdek_account)
        order_uuid = action.get("uuid", "")
        if not order_uuid:
            order = await asyncio.to_thread(find_cdek_order, order_id, token, by="im_number")
            order_uuid = str(order.get("uuid") or "")
        if not order_uuid:
            return None
        action.update(status="updating", uuid=order_uuid, number=new_im_number)
        if delivery:
            await delivery.checkpoint()
        await asyncio.to_thread(update_cdek_order_number, order_uuid, new_im_number, token)
        if delivery:
            action["status"] = "updated"
            delivery.state["im_number"] = new_im_number
            await delivery.checkpoint()
    new_im_number = action["number"]
    await update_tilda_submission_im_number(
        database_target, site_name, submission_id, order_identity(payload), new_im_number
    )
    if delivery:
        action["status"] = "saved"
        await delivery.checkpoint()
    return new_im_number


def _build_custom_email(payload: TildaEmailSendRequest) -> tuple[str, str, str]:
    subject = (payload.custom_subject or "Сообщение по вашему заказу").strip()
    body = (payload.custom_body or "").strip()
    body_html = (
        '<div style="font-family:Arial,sans-serif;line-height:1.6;color:#14171f;">'
        + html.escape(body).replace("\n", "<br>")
        + "</div>"
    )
    return subject, body, body_html


_tilda_outbox: TildaOutbox | None = None


async def _recover_tilda_database(target):
    await asyncio.to_thread(recover_database_pool, target)


def _sheets_backup_client():
    secret = config.TILDA_SHEETS_SERVICE_ACCOUNT_JSON
    return GoogleSheetsBackup(config.TILDA_SHEETS_SPREADSHEET_ID,
                              secret.get_secret_value() if secret else "", config.TILDA_SHEETS_TAB)


async def _send_sheets_backup(document):
    return await asyncio.to_thread(_sheets_backup_client().send, document)


def _submission_response(result):
    if result.get("queued") and _get_tilda_outbox().backup.enabled and result.get("external_backup", {}).get("status") != "saved":
        return JSONResponse({**result, "ok": False, "error": "external_backup_pending"},
                            status_code=503, headers={"Retry-After": "60", **PRIVATE_FORM_HEADERS})
    return JSONResponse(result, headers=PRIVATE_FORM_HEADERS)


def _get_tilda_outbox() -> TildaOutbox:
    global _tilda_outbox
    if _tilda_outbox is None:
        _tilda_outbox = TildaOutbox(config.TILDA_QUEUE_PATH, _deliver_queued_submission,
                                  prepare=create_tilda_submissions_schema, recover=_recover_tilda_database,
                                  backup=_send_sheets_backup if (config.TILDA_SHEETS_SPREADSHEET_ID or config.TILDA_SHEETS_SERVICE_ACCOUNT_JSON) else None)
    return _tilda_outbox


@asynccontextmanager
async def tilda_storage_lifespan():
    global _tilda_outbox
    targets = {config.TILDA_APEX_DATABASE_TARGET, config.TILDA_REAL_BABY_DATABASE_TARGET,
               config.TILDA_AMA_LIVING_DATABASE_TARGET, config.TILDA_WAYFS_DATABASE_TARGET,
               config.TILDA_FERALRU_DATABASE_TARGET, config.TILDA_SHELWEAR_DATABASE_TARGET}
    if config.TILDA_PROJECTS_JSON.strip():
        targets.update(_project_config_maps()[1].values())
    outbox = _get_tilda_outbox()
    try:
        # Opening min_size=0 pools starts no blocking connection checks.
        await asyncio.to_thread(open_database_pools, *targets, wait=False)
        await outbox.start(targets)
        yield
    finally:
        await outbox.stop()
        await asyncio.to_thread(close_database_pools)
        if _tilda_outbox is outbox:
            _tilda_outbox = None


@router.options("/tilda/{name}/webhook")
async def tilda_webhook_options(name: str):
    _site_name_or_404(name)
    return Response(status_code=204)


@router.post("/tilda/{name}/webhook")
async def tilda_webhook(name: str, request: Request):
    trace_id = uuid.uuid4().hex[:12]

    _log_webhook(
        logging.INFO,
        name,
        trace_id,
        "Получен webhook-запрос",
    )

    site_name = _site_name_or_404(name)
    payload, payload_type = await _extract_payload(request)
    order_id = _order_id_from_payload(payload)

    _log_webhook(
        logging.INFO,
        name,
        trace_id,
        "Данные заказа получены",
        order_id=order_id,
    )

    if payload_type == "too_large":
        _log_webhook(
            logging.WARNING,
            name,
            trace_id,
            "Webhook отклонён: слишком большой размер данных",
            order_id=order_id,
        )
        return JSONResponse(
            {"ok": False, "error": "payload_too_large"}, status_code=413
        )

    if _is_tilda_test(payload):
        _log_webhook(
            logging.INFO,
            name,
            trace_id,
            "Получен тестовый webhook Tilda",
            order_id=order_id,
        )
        return JSONResponse(
            {"ok": True, "site": site_name, "message": "Tilda webhook test received"}
        )

    _database_target_or_404(site_name)
    try:
        submission_id, created = await _save_submission(site_name, request, payload, payload_type)
        result = await _get_tilda_outbox().process(submission_id)
    except Exception as exc:
        _log_webhook(logging.ERROR, name, trace_id,
                     "Не удалось подтвердить локальное хранение заказа: отправителю нужно повторить webhook",
                     order_id=order_id, error=type(exc).__name__)
        return JSONResponse({"ok": False, "error": "local_storage_unavailable"},
                            status_code=503, headers={"Retry-After": "60"})
    if not created:
        result = {**result, "duplicate": True}
    if result.get("queued"):
        _log_webhook(logging.WARNING, name, trace_id,
                     "Заказ принят и сохранён локально, ожидает записи в БД; повторная попытка через 60 секунд",
                     order_id=order_id, submission_id=submission_id)
    return _submission_response(result)


async def _deliver_queued_submission(item: Delivery) -> dict[str, Any]:
    database_target = item.target
    site_name = name = item.submission["site"]
    payload = item.submission["payload"]
    trace_id = item.id
    order_id = _order_id_from_payload(payload)
    if "database_id" not in item.state:
        saved_id, created = await save_tilda_submission(database_target, item.submission)
        # A commit may have succeeded before a connection dropped or the process
        # stopped. An existing row with our original ID is still this delivery.
        item.state.update(database_id=saved_id, owned=created or saved_id == item.id)
        await item.checkpoint()
    submission_id = item.state["database_id"]
    if not item.state["owned"]:
        return {"ok": True, "site": site_name, "submission_id": submission_id,
                "duplicate": True, "queued": False, "storage": "database"}
    if item.submission.get("restored"):
        return {"ok": True, "site": site_name, "submission_id": submission_id,
                "restored": True, "queued": False, "storage": "database"}
    _log_webhook(logging.INFO, name, trace_id, "Заказ подтверждён в БД",
                 order_id=order_id, submission_id=submission_id)

    auto_actions: dict[str, Any] = {"email_sent": False, "im_number_updated": False}

    try:
        email_message = await _send_order_notification_for_submission(
            database_target, site_name, submission_id, payload, delivery=item
        )
        auto_actions["email_sent"] = bool(email_message)

        _log_webhook(
            logging.INFO,
            name,
            trace_id,
            (
                "Письмо клиенту отправлено"
                if email_message
                else "Автоматическое письмо не отправлено: " + item.state.get("email", {}).get("status", "skipped")
            ),
            order_id=order_id,
            submission_id=submission_id,
        )
    except (*DATABASE_ERRORS, sqlite3.Error, LeaseLost):
        raise
    except Exception as exc:
        item.state["email"]["error"] = type(exc).__name__
        await item.checkpoint()
        auto_actions["email_error"] = _short_webhook_error(exc)
        _log_webhook(
            logging.ERROR,
            name,
            trace_id,
            "Ошибка отправки письма клиенту",
            order_id=order_id,
            submission_id=submission_id,
            error=_short_webhook_error(exc),
        )

    try:
        new_im_number = await _auto_update_cdek_im_number(
            database_target, site_name, submission_id, payload, delivery=item
        )
        auto_actions["im_number_updated"] = bool(new_im_number)

        if new_im_number:
            auto_actions["new_im_number"] = new_im_number
            cdek_step = "Номер ИМ в СДЭК обновлён"
        else:
            cdek_step = "Обновление номера ИМ в СДЭК не потребовалось"

        _log_webhook(
            logging.INFO,
            name,
            trace_id,
            cdek_step,
            order_id=order_id,
            submission_id=submission_id,
        )
    except (*DATABASE_ERRORS, sqlite3.Error, LeaseLost):
        raise
    except Exception as exc:
        item.state.setdefault("cdek", {}).update(status="failed", error=type(exc).__name__)
        await item.checkpoint()
        auto_actions["im_number_error"] = _short_webhook_error(exc)
        _log_webhook(
            logging.ERROR,
            name,
            trace_id,
            "Ошибка обновления номера ИМ в СДЭК",
            order_id=order_id,
            submission_id=submission_id,
            error=_short_webhook_error(exc),
        )

    _log_webhook(
        logging.INFO,
        name,
        trace_id,
        "Обработка webhook завершена",
        order_id=order_id,
        submission_id=submission_id,
    )

    if item.state.get("email", {}).get("status") in {"unknown", "failed"}:
        auto_actions["email_status"] = item.state["email"]["status"]
    return {"ok": True, "site": site_name, "submission_id": submission_id,
            "auto_actions": auto_actions, "queued": False, "storage": "database"}


@router.get("/tilda/{name}/form", response_class=HTMLResponse)
async def tilda_form_page(name: str, request: Request):
    site_name = _site_name_or_404(name)
    _form_secret_or_403(site_name, request)
    return templates.TemplateResponse(
        request=request,
        name="tilda/form.html",
        context={"site_name": site_name, "assets_version": FORM_ASSETS_VERSION,
                 "backup_url": _sheets_backup_client().url},
        headers=PRIVATE_FORM_HEADERS,
    )


@router.get("/tilda/{name}/form/submissions")
async def tilda_form_submissions(name: str, request: Request):
    site_name = _site_name_or_404(name)
    _form_secret_or_403(site_name, request)
    try:
        submissions, available, pending = await _read_submission_state(site_name)
    except (sqlite3.Error, OSError) as exc:
        _log_webhook(logging.ERROR, site_name, "", "Локальная очередь недоступна", error=type(exc).__name__)
        return JSONResponse({"ok": False, "error": "local_storage_unavailable"}, status_code=503,
                            headers={**PRIVATE_FORM_HEADERS, "Retry-After": "60"})
    return JSONResponse(
        {
            "ok": True,
            "site": site_name,
            "submissions": submissions,
            "database_available": available,
            "pending_count": pending,
        },
        headers=PRIVATE_FORM_HEADERS,
    )


@router.post("/tilda/{name}/form/submissions")
async def tilda_create_submission(name: str, request: Request):
    site = _site_name_or_404(name)
    _form_secret_or_403(site, request)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 2 * MAX_BODY_BYTES:
            return JSONResponse({"ok": False, "error": "body_too_large"}, status_code=413)
        body.extend(chunk)
    try:
        raw = json.loads(body.decode("utf-8"))
        json.dumps(raw, allow_nan=False, ensure_ascii=False).encode("utf-8")
        value = TildaSubmissionCreate.model_validate(raw)
        if not value.payload:
            raise ValueError()
    except (ValueError, TypeError, RecursionError, ValidationError):
        return JSONResponse({"ok": False, "error": "invalid_record"}, status_code=422, headers=PRIVATE_FORM_HEADERS)
    if value.site is not None and value.site != site:
        return JSONResponse({"ok": False, "error": "record_site_mismatch"}, status_code=422)
    key = webhook_key(value.payload)
    if value.webhook_key and value.webhook_key != key:
        return JSONResponse({"ok": False, "error": "record_webhook_key_mismatch"}, status_code=422)
    record = value.model_dump(exclude_none=True)
    record.update(id=value.id or f"{_now_utc().strftime('%Y-%m-%d_%H-%M-%S')}_{uuid.uuid4().hex[:8]}",
                  site=site, restored=True)
    outbox = _get_tilda_outbox()
    try:
        await outbox.initialize()
        previous = await asyncio.to_thread(outbox._row, record["id"])
        if previous and (previous["site"] != site or previous["webhook_key"] != key):
            return JSONResponse({"ok": False, "error": "record_id_conflict"}, status_code=409)
        target = _database_target_or_404(site)
        if outbox.blocked_until.get(target, 0) <= time.time():
            try:
                identity = await read_tilda_submission_identity(target, record["id"])
                if identity is not None and identity != (site, key):
                    return JSONResponse({"ok": False, "error": "record_id_conflict"}, status_code=409)
            except DATABASE_ERRORS:
                outbox.database_unavailable(target)
        item_id, created = await outbox.enqueue(target, record)
        result = await outbox.process(item_id)
        if not created:
            result = {**result, "duplicate": True}
        return _submission_response(result)
    except sqlite3.IntegrityError:
        return JSONResponse({"ok": False, "error": "record_id_conflict"}, status_code=409)
    except (OSError, sqlite3.Error):
        return JSONResponse({"ok": False, "error": "local_storage_unavailable"}, status_code=503,
                            headers={"Retry-After": "60", **PRIVATE_FORM_HEADERS})


@router.post("/tilda/{name}/form/email/send")
async def tilda_send_email(name: str, request: Request, payload: TildaEmailSendRequest):
    site_name = _site_name_or_404(name)
    _form_secret_or_403(site_name, request)

    submission_id = payload.submission_id.strip()
    to_email = payload.to_email.strip().lower()
    message_type = payload.message_type.strip()
    if not submission_id:
        return JSONResponse(
            {"ok": False, "error": "submission_id_required"}, status_code=400
        )
    if not to_email or "@" not in to_email:
        return JSONResponse(
            {"ok": False, "error": "valid_email_required"}, status_code=400
        )
    try:
        rows, available, _ = await _read_submission_state(site_name)
    except (sqlite3.Error, OSError):
        return JSONResponse({"ok": False, "error": "local_storage_unavailable"}, status_code=503)
    if not available:
        return JSONResponse({"ok": False, "error": "database_unavailable", "detail": "БД недоступна. Отправка будет доступна после восстановления."},
                            status_code=503, headers={"Retry-After": "60"})
    submission = next((item for item in rows if item.get("id") == submission_id), None)
    if submission is None:
        return JSONResponse({"ok": False, "error": "submission_not_found"}, status_code=404)
    if submission.get("storage_status") == "queued":
        return JSONResponse({"ok": False, "error": "order_waiting_for_database"}, status_code=409)
    if message_type == "order_notification":
        subject, body, body_html = _build_order_email(payload, site_name, submission)
    elif message_type == "custom_message":
        subject, body, body_html = _build_custom_email(payload)
        if not body:
            return JSONResponse(
                {"ok": False, "error": "custom_body_required"}, status_code=400
            )
    else:
        return JSONResponse(
            {"ok": False, "error": "unknown_message_type"}, status_code=400
        )

    try:
        await asyncio.to_thread(send_customer_email, to_email, subject, body, body_html,
                                sender_name=_email_sender_name(site_name))
        message = await save_tilda_email_message(
            _database_target_or_404(site_name),
            {
                "id": f"{_now_utc().strftime('%Y-%m-%d_%H-%M-%S')}_{uuid.uuid4().hex[:8]}",
                "submission_id": submission_id,
                "site": site_name,
                "created_at": _now_utc().isoformat(),
                "message_type": message_type,
                "to_email": to_email,
                "subject": subject,
                "body": body,
                "body_html": body_html,
                "status": "sent",
            },
        )
    except DATABASE_ERRORS as exc:
        _log_webhook(logging.ERROR, site_name, "", "Не удалось сохранить историю отправленного письма",
                     submission_id=submission_id, error=_short_webhook_error(exc))
        return JSONResponse({"ok": False, "error": "email_history_database_unavailable",
                             "email_sent": True, "detail": "Письмо отправлено, но БД не подтвердила историю. Не отправляйте повторно."}, status_code=503)
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": "email_send_failed", "detail": _short_webhook_error(exc)},
            status_code=502,
        )

    return JSONResponse({"ok": True, "site": site_name, "message": message})

    
@router.post("/tilda/{name}/form/cdek/im-number")
async def tilda_update_cdek_im_number(
    name: str, request: Request, payload: CdekImNumberUpdateRequest
):
    site_name = _site_name_or_404(name)
    _form_secret_or_403(site_name, request)

    current_im_number = payload.current_im_number.strip()
    new_im_number = payload.new_im_number.strip()
    order_id = (payload.order_id or "").strip()
    order_uuid = (payload.order_uuid or "").strip()
    submission_id = (payload.submission_id or "").strip()

    if not new_im_number:
        return JSONResponse(
            {"ok": False, "error": "new_im_number_required"}, status_code=400
        )
    if not order_uuid and not current_im_number:
        return JSONResponse(
            {"ok": False, "error": "current_im_number_or_uuid_required"},
            status_code=400,
        )
    if submission_id:
        rows, available, _ = await _read_submission_state(site_name)
        if not available:
            return JSONResponse({"ok": False, "error": "database_unavailable"}, status_code=503,
                                headers={"Retry-After": "60"})
        submission = next((row for row in rows if row["id"] == submission_id), None)
        if not submission or submission["storage_status"] == "queued":
            return JSONResponse({"ok": False, "error": "order_not_ready"}, status_code=409)

    try:
        cdek_account = _cdek_account_or_404(site_name)
        token = await asyncio.to_thread(cdek_get_token, cdek_account)
        order = None
        if not order_uuid:
            order = await asyncio.to_thread(find_cdek_order, current_im_number, token, by="im_number")
            order_uuid = str(order.get("uuid") or "")
        if not order_uuid:
            return JSONResponse(
                {"ok": False, "error": "cdek_order_uuid_not_found"},
                status_code=404,
            )
        request_uuid = await asyncio.to_thread(update_cdek_order_number, order_uuid, new_im_number, token)
        await update_tilda_submission_im_number(
            _database_target_or_404(site_name),
            site_name,
            submission_id,
            order_id or current_im_number,
            new_im_number,
        )
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": "cdek_update_failed", "detail": _short_webhook_error(exc)},
            status_code=502,
        )

    return JSONResponse(
        {
            "ok": True,
            "site": site_name,
            "order_uuid": order_uuid,
            "request_uuid": request_uuid,
            "old_im_number": current_im_number,
            "new_im_number": new_im_number,
        }
    )
