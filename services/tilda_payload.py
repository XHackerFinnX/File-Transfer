"""Read Tilda and Taplink orders without changing the stored webhook body.

The Taplink adapter exposes the fields used by the existing Tilda dashboard and
mail templates. Technical IDs remain separate from customer-facing numbers.
"""

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any


def first_value(source: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = source.get(key)
        if value is not None and value != "":
            return value
    return ""


def _structured(value: Any, expected: type) -> Any:
    # Taplink may send nested JSON as strings in URL-encoded forms.
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            pass
    return value if isinstance(value, expected) else expected()


def payment_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return _structured(first_value(payload, ["payment", "Payment", "Оплата"]), dict)


def is_taplink_payload(payload: dict[str, Any]) -> bool:
    return "offers" in payload or (
        "shipping_fields" in payload and any(
            key in payload for key in ("order_id", "order_number", "lead_id")
        )
    )


def order_identity(payload: dict[str, Any]) -> str:
    """Stable identity for retries; never deduplicate Taplink by order_number."""
    payment = payment_payload(payload)
    if is_taplink_payload(payload):
        return str(first_value(payload, ["order_id", "lead_id"])).strip()
    return str(
        first_value(payment, ["orderid", "order_id"])
        or first_value(payload, ["orderid", "order_id", "Order ID", "Номер заказа"])
    ).strip()


def decimal_value(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(re.sub(r"\s", "", str(value)).replace(",", "."))
    except InvalidOperation:
        return None
    # Bound the exponent before formatting/multiplying untrusted numbers.
    return result if result.is_finite() and -12 <= result.adjusted() < 15 else None


def _number(value: Decimal | None) -> str:
    return format(value, "f") if value is not None else ""


def _labelled_values(lines: Any) -> dict[str, Any]:
    result = {}
    for line in lines if isinstance(lines, list) else []:
        if isinstance(line, str) and ":" in line:
            label, value = line.split(":", 1)
            result[label.strip().casefold()] = value.strip()
    return result


def _options(value: Any) -> list[dict[str, Any]]:
    options = []
    for item in _structured(value, list):
        if isinstance(item, str):
            label, separator, variant = item.partition(":")
            options.append({
                "option": label.strip(),
                "variant": variant.strip() if separator else "",
            })
        elif isinstance(item, dict):
            options.append({
                "option": first_value(item, ["option", "name"]),
                "variant": first_value(item, ["variant", "value"]),
            })
    return options


def normalize_order_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a Tilda-compatible view, preserving the original input verbatim."""
    if not is_taplink_payload(payload):
        return payload

    extended = {
        str(item.get("name")): item.get("value")
        for item in _structured(payload.get("records_extended"), list)
        if isinstance(item, dict)
    }
    contacts = _labelled_values(extended.get("contacts"))
    for record in _structured(payload.get("records"), list):
        if isinstance(record, dict):
            contacts[str(record.get("title", "")).strip().casefold()] = record.get("value")
    name = first_value(payload, ["fullname", "Name", "name", "fio"]) or first_value(
        contacts, ["фио", "имя", "name"]
    )
    email = first_value(payload, ["email", "Email"]) or first_value(
        contacts, ["email", "e-mail", "почта"]
    )
    phone = first_value(payload, ["phone", "Phone"]) or first_value(
        contacts, ["телефон", "phone"]
    )
    shipping = _structured(payload.get("shipping_fields"), dict)
    shipping_lines = _labelled_values(extended.get("shipping"))

    products = []
    for offer in _structured(payload.get("offers"), list):
        if not isinstance(offer, dict):
            continue
        quantity = decimal_value(first_value(offer, ["amount", "quantity"]))
        if quantity is None or quantity <= 0:
            quantity = Decimal(1)
        line_total = decimal_value(offer.get("budget"))
        unit_price = decimal_value(offer.get("price_discount"))
        if unit_price is None:
            unit_price = (
                line_total / quantity if line_total is not None
                else decimal_value(offer.get("price"))
            )
        if line_total is None and unit_price is not None:
            line_total = unit_price * quantity
        products.append({
            "name": first_value(offer, ["title", "name"]),
            "sku": first_value(offer, ["sku", "offer_id", "product_id"]),
            "externalid": first_value(offer, ["offer_id", "product_id"]),
            "quantity": _number(quantity.normalize()),
            "price": _number(unit_price),
            "original_price": offer.get("price", ""),
            "line_total": _number(line_total),
            "options": _options(offer.get("options")),
        })
    subtotal = sum(
        (decimal_value(product["line_total"]) or Decimal(0) for product in products),
        Decimal(0),
    )
    shipping_price = decimal_value(first_value(payload, ["shipping_price"]))
    if shipping_price is None:
        shipping_price = decimal_value(extended.get("shipping_price"))
    total = decimal_value(payload.get("budget"))
    if total is None:
        total = decimal_value(extended.get("budget"))
    if total is None and products:
        total = subtotal + (shipping_price or Decimal(0))
    number = (
        first_value(payload, ["order_number", "lead_number"])
        or first_value(extended, ["order_number", "lead_number"])
        or order_identity(payload)
    )
    address = ", ".join(
        str(shipping[key]).strip() for key in ("addr1", "addr2") if shipping.get(key)
    )
    delivery = first_value(shipping, ["shipping_method"]) or first_value(
        shipping_lines, ["опции доставки", "способ доставки"]
    )
    payment = {
        "orderid": str(number).strip(),
        "sys": first_value(payload, ["payment_method_title", "payment_method"]),
        "amount": _number(total),
        "subtotal": _number(subtotal) if products else "",
        "products": products,
        "delivery": delivery,
        "delivery_price": _number(shipping_price),
        "delivery_fio": first_value(shipping, ["fullname", "fio", "name"]) or name,
        "delivery_address": address or first_value(shipping_lines, ["адрес"]),
        "delivery_city": first_value(shipping, ["city"]) or first_value(
            shipping_lines, ["город"]
        ),
        "delivery_region": first_value(shipping, ["state", "region"]) or first_value(
            shipping_lines, ["область или край", "регион"]
        ),
        "delivery_zip": first_value(shipping, ["zip", "postcode", "postal_code"]) or first_value(
            shipping_lines, ["индекс"]
        ),
        "delivery_pickup_id": first_value(shipping, ["pickup_id", "point_id"]),
        "delivery_comment": first_value(shipping, ["comment"]),
    }
    return {**payload, "Name": name, "Email": email, "Phone": phone, "payment": payment}


def order_number(payload: dict[str, Any]) -> str:
    normalized = normalize_order_payload(payload)
    return str(
        first_value(payment_payload(normalized), ["orderid", "order_id"])
        or order_identity(payload)
    ).strip()


def can_auto_update_cdek(payload: dict[str, Any]) -> bool:
    # Keep legacy Tilda behaviour. A Taplink order can use another carrier;
    # an explicit CDEK delivery is required before contacting the CDEK account.
    if not is_taplink_payload(payload):
        return True
    delivery = payment_payload(normalize_order_payload(payload)).get("delivery", "")
    return bool(re.search(r"сд[еэ]к|cdek", str(delivery), re.IGNORECASE))
