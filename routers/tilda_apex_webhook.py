import html
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
from pydantic import BaseModel

from config import config
from db.tilda_orders import (
    read_tilda_submissions,
    save_tilda_email_message,
    save_tilda_submission,
    update_tilda_submission_im_number,
)
from services.tilda_cdek import (
    cdek_get_token,
    find_cdek_order,
    send_customer_email,
    update_cdek_order_number,
)

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_SUBMISSIONS_IN_LIST = 500
TILDA_SITE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

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
        "im_number": str(submission.get("im_number") or ""),
        "email_messages": submission.get("email_messages") or [],
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
            return {
                str(key): _json_safe(value) for key, value in parsed.items()
            }, "json"
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
    await save_tilda_submission(_database_target_or_404(name), meta)
    return submission_id


async def _read_submissions(name: str) -> list[dict[str, Any]]:
    submissions = await read_tilda_submissions(
        _database_target_or_404(name),
        _site_name_or_404(name),
        MAX_SUBMISSIONS_IN_LIST,
    )
    return [_submission_summary(submission) for submission in submissions]


def _first_value(source: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return ""


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _format_detail_value(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value).strip()


def _payment_payload(order_payload: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(
        order_payload.get("payment")
        or order_payload.get("Payment")
        or order_payload.get("Оплата")
    )


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
    order_payload = _as_dict((submission or {}).get("payload"))
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


def _build_custom_email(payload: TildaEmailSendRequest) -> tuple[str, str, str]:
    subject = (payload.custom_subject or "Сообщение по вашему заказу").strip()
    body = (payload.custom_body or "").strip()
    body_html = (
        '<div style="font-family:Arial,sans-serif;line-height:1.6;color:#14171f;">'
        + html.escape(body).replace("\n", "<br>")
        + "</div>"
    )
    return subject, body, body_html


@router.options("/tilda/{name}/webhook")
async def tilda_webhook_options(name: str):
    _site_name_or_404(name)
    return Response(status_code=204)


@router.post("/tilda/{name}/webhook")
async def tilda_webhook(name: str, request: Request):
    site_name = _site_name_or_404(name)
    payload, payload_type = await _extract_payload(request)

    if payload_type == "too_large":
        return JSONResponse(
            {"ok": False, "error": "payload_too_large"}, status_code=413
        )

    if _is_tilda_test(payload):
        return JSONResponse(
            {"ok": True, "site": site_name, "message": "Tilda webhook test received"}
        )

    submission_id = await _save_submission(site_name, request, payload, payload_type)
    print(f"Данные записаны: Проект {name} {site_name}. submission_id: {submission_id}")
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
    if message_type == "order_notification":
        submission = next(
            (
                item
                for item in await _read_submissions(site_name)
                if item.get("id") == submission_id
            ),
            None,
        )
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
        send_customer_email(to_email, subject, body, body_html)
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
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": "email_send_failed", "detail": str(exc)},
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

    try:
        cdek_account = _cdek_account_or_404(site_name)
        token = cdek_get_token(cdek_account)
        order = None
        if not order_uuid:
            order = find_cdek_order(current_im_number, token, by="im_number")
            order_uuid = str(order.get("uuid") or "")
        if not order_uuid:
            return JSONResponse(
                {"ok": False, "error": "cdek_order_uuid_not_found"},
                status_code=404,
            )
        request_uuid = update_cdek_order_number(order_uuid, new_im_number, token)
        await update_tilda_submission_im_number(
            _database_target_or_404(site_name),
            site_name,
            submission_id,
            order_id or current_im_number,
            new_im_number,
        )
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": "cdek_update_failed", "detail": str(exc)},
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