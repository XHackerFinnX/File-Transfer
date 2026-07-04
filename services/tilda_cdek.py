"""
Helpers for the Tilda -> CDEK handoff.

Flow:
1. Receive and store a Tilda payment webhook in routers/tilda_apex_webhook.py.
2. Build a CDEK order payload from the Tilda/Tinkoff payment payload.
3. Create or find an existing CDEK order.
4. Render a PDF with CDEK package barcodes.
5. Send the customer-facing "order accepted" email through your own mail provider.

CDEK does not provide an API method for arbitrary customer emails. Its email,
SMS, PUSH and webhook features are service notifications/events with CDEK-defined
content, so transactional customer mail must be sent by SMTP, SendGrid,
Unisender, or another mail service controlled by the shop.
"""

from __future__ import annotations

import os
import re
import smtplib
import time
from email.mime.text import MIMEText
from typing import Any

from config import config

import requests

CDEK_BASE_URL = "https://api.cdek.ru/v2"
CDEK_CLIENT_ID = config.CDEK_CLIENT_ID.get_secret_value()
CDEK_CLIENT_SECRET = config.CDEK_CLIENT_SECRET.get_secret_value()

CDEK_TARIFF_CODE = 136

SENDER_LOCATION = {"address": config.SENDER_LOCATION.get_secret_value()}
DEFAULT_ITEM_WEIGHT_GRAMS = 700
CDEK_ORDER_NUMBER_PREFIX = ""

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.example.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "shop@example.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "пароль_или_api_key")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)


def cdek_get_token() -> str:
    """Request a CDEK OAuth token for server-to-server calls."""
    response = requests.post(
        f"{CDEK_BASE_URL}/oauth/token",
        params={
            "grant_type": "client_credentials",
            "client_id": CDEK_CLIENT_ID,
            "client_secret": CDEK_CLIENT_SECRET,
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def normalize_phone(raw: str) -> str:
    """Convert '+7 (985) 895-00-11' to '+79858950011'."""
    return re.sub(r"[^\d+]", "", raw or "")


def build_item_name(product: dict[str, Any]) -> str:
    """Merge the product title and options into the CDEK item name.

    CDEK barcode contents are not customizable: /v2/print/barcodes encodes the
    CDEK waybill/package number. Product details must therefore be placed in
    packages[].items[].name and amount so they are printed as text on the label.
    """
    parts = [str(product.get("name") or product.get("title") or "Товар")]
    parts.extend(
        str(option.get("variant") or option.get("value") or "")
        for option in product.get("options", [])
        if option.get("variant") or option.get("value")
    )
    return ", ".join(part for part in parts if part)


def _payment(webhook_data: dict[str, Any]) -> dict[str, Any]:
    payload = webhook_data.get("payload", {})
    payment = payload.get("payment") or payload.get("Payment") or {}
    return payment if isinstance(payment, dict) else {}


def cdek_order_number(order_id: str | int, prefix: str | None = None) -> str:
    """Return the shop order number displayed in CDEK as "Номер ИМ".

    CDEK takes this value from the order payload field named "number". Set
    CDEK_ORDER_NUMBER_PREFIX=apexf1_ to send values like apexf1_1239185829
    instead of the raw Tilda/Tinkoff orderid.
    """
    clean_order_id = str(order_id or "").strip()
    clean_prefix = CDEK_ORDER_NUMBER_PREFIX if prefix is None else prefix
    return f"{clean_prefix}{clean_order_id}" if clean_order_id else ""


def build_cdek_order_payload(webhook_data: dict[str, Any]) -> dict[str, Any]:
    """Map the Tilda/Tinkoff payment webhook into POST /v2/orders payload."""
    payment = _payment(webhook_data)
    products = payment.get("products") if isinstance(payment.get("products"), list) else []
    items = [
        {
            "name": build_item_name(product),
            "ware_key": product.get("sku") or product.get("externalid"),
            "payment": {"value": 0},
            "cost": float(product.get("price") or 0),
            "amount": int(product.get("quantity") or product.get("amount") or 1),
            "weight": int(product.get("weight") or DEFAULT_ITEM_WEIGHT_GRAMS),
        }
        for product in products
    ]

    return {
        "type": 1,
        "number": cdek_order_number(payment.get("orderid") or payment.get("order_id") or ""),
        "tariff_code": CDEK_TARIFF_CODE,
        "delivery_point": payment.get("delivery_pickup_id"),
        "from_location": SENDER_LOCATION,
        "recipient": {
            "name": webhook_data.get("customer_name") or payment.get("delivery_fio") or "",
            "phones": [{"number": normalize_phone(str(webhook_data.get("contact") or ""))}],
            "email": webhook_data.get("payload", {}).get("Email"),
        },
        "packages": [
            {
                "number": "1",
                "weight": sum(item["weight"] * item["amount"] for item in items) or DEFAULT_ITEM_WEIGHT_GRAMS,
                "items": items,
            }
        ],
        "print": "BARCODE",
    }


def create_cdek_order(payload: dict[str, Any], token: str) -> str:
    """Register a CDEK order and return the CDEK order UUID."""
    response = requests.post(
        f"{CDEK_BASE_URL}/orders",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["entity"]["uuid"]



def update_cdek_order(payload: dict[str, Any], token: str) -> str:
    """Update an existing CDEK order and return the CDEK request UUID.

    According to the CDEK OpenAPI spec, order editing is PATCH /v2/orders.
    The order UUID is part of OrderUpdateRequestDto, not a path parameter.
    To change the value displayed in the personal account as "Номер ИМ", send
    the order UUID and desired "number". CDEK allows this only while the cargo
    has no warehouse movement yet, i.e. while the order status is "Создан".
    """
    response = requests.patch(
        f"{CDEK_BASE_URL}/orders",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["entity"]["uuid"]


def update_cdek_order_number(uuid: str, new_number: str, token: str) -> str:
    """Rename CDEK "Номер ИМ" for an already created order."""
    return update_cdek_order({"uuid": uuid, "number": new_number}, token)


def find_cdek_order(identifier: str, token: str, by: str = "im_number") -> dict[str, Any]:
    """Find an already registered CDEK order by shop number or CDEK number."""
    response = requests.get(
        f"{CDEK_BASE_URL}/orders",
        params={by: identifier},
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()["entity"]


def _as_print_order(identifier: str | int) -> dict[str, Any]:
    value = str(identifier)
    return {"cdek_number": int(value)} if value.isdigit() else {"order_uuid": value}


def print_barcodes_for_orders(
    order_identifiers: list[str | int], token: str, timeout: int = 30
) -> str:
    """Render one PDF with barcode labels for up to 100 existing CDEK orders."""
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.post(
        f"{CDEK_BASE_URL}/print/barcodes",
        json={"orders": [_as_print_order(item) for item in order_identifiers], "format": "A6"},
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    barcode_uuid = response.json()["entity"]["uuid"]

    deadline = time.time() + timeout
    while time.time() < deadline:
        info_response = requests.get(
            f"{CDEK_BASE_URL}/print/barcodes/{barcode_uuid}", headers=headers, timeout=20
        )
        info_response.raise_for_status()
        url = info_response.json().get("entity", {}).get("url")
        if url:
            return url
        time.sleep(2)
    raise TimeoutError("CDEK barcode PDF was not ready before timeout")


def send_order_accepted_email(to_email: str, customer_name: str, order_number: str) -> None:
    """Send the customer-facing accepted-order email through the shop SMTP."""
    if not to_email:
        return

    message = MIMEText(
        f"Здравствуйте, {customer_name}!\n\n"
        f"Ваш заказ №{order_number} принят и готовится к отправке.\n"
        f"Трек-номер СДЭК пришлём отдельным письмом.",
        _charset="utf-8",
    )
    message["Subject"] = f"Заказ №{order_number} принят"
    message["From"] = SMTP_FROM
    message["To"] = to_email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(message)