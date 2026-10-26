"""Shop webhook regressions. SMTP and CDEK are always mocked.

Optional SQL checks use a private schema in TEST_TILDA_DATABASE_DSN.
"""

import copy
import json
import os
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import the application with test credentials, without opening database pools.
TEST_ENV = {
    "TURN_SECRET": "test-only", "POSTGRESQL_HOST": "127.0.0.1",
    "POSTGRESQL_USER": "test", "POSTGRESQL_PASSWORD": "test-only", "SMTP_PASSWORD": "",
    **{"POSTGRESQL_DATABASE" + suffix: "unused_test_database" for suffix in (
        "", "_APEX", "_REAL_BABY", "_AMA_LIVING", "_WAYFS", "_FERALRU", "_SHELWEAR",
    )},
}
with patch.dict(os.environ, TEST_ENV):
    from db import schemas, tilda_orders as storage
    from routers import tilda_apex_webhook as shop

from services.tilda_payload import (
    can_auto_update_cdek, decimal_value, normalize_order_payload, order_identity, order_number,
)
from services import tilda_cdek
from services.tilda_outbox import TildaOutbox


def taplink_order():
    return {
        "ip": "192.0.2.10", "email": "buyer@example.test", "phone": "79000000000",
        "addons": {"2": {"options": []}}, "budget": "30",
        "offers": [{
            "price": "3000", "title": 'Кроп "PURE BABY" белый', "amount": "1",
            "budget": "30", "weight": "0.25", "options": ["Размер: S"],
            "offer_id": "21513768", "picture_id": "67735919", "product_id": "12685660",
            "price_discount": "30",
        }],
        "lead_id": "56140061", "fullname": "Тестовый Покупатель", "order_id": "27956212",
        "records": [{"idx": "1", "type": "3", "title": "ФИО", "value": "Тестовый Покупатель"}],
        "discounts": {"179776": "ТЕСТ99"}, "lead_number": "27", "order_number": "27",
        "currency_code": "RUB", "payment_method": "bankcard", "shipping_price": "0",
        "shipping_fields": json.dumps({
            "shipping_method": "Доставка Яндекс ПВЗ (оплата за доставку при получении)",
            "state": "Тестовый регион", "city": "Тестовый город", "addr1": "Тестовая улица, 1",
        }),
        "records_extended": [
            {"name": "budget", "type": "number", "value": "30"},
            {"name": "shipping", "type": "text", "value": ["Город: Резервный город"]},
        ],
        "payment_method_title": "Банковская карта", "order_status_id": "2",
    }


def tilda_order():
    return {
        "Name": "Тестовый Покупатель", "Email": "buyer@example.test", "Phone": "79000000000",
        "payment": {
            "sys": "tinkoff", "amount": "6088.77", "orderid": "1301626769",
            "delivery": "Доставка в ПВЗ СДЭК", "delivery_price": 688.77,
            "subtotal": "5400", "delivery_fio": "Тестовый Покупатель", "delivery_city": "Город",
            "delivery_address": "Тестовая улица, 1", "delivery_pickup_id": "TEST1",
            "products": [{
                "name": "Зип-Худи", "sku": "hoodie-L", "price": "5400", "amount": 5400,
                "quantity": 1, "options": [{"option": "Размер", "variant": "L"}],
            }],
        },
    }


class PayloadTests(unittest.TestCase):
    def test_taplink_envelopes_and_older_form_records_expose_the_order(self):
        raw = taplink_order()
        encoded = json.dumps(raw, ensure_ascii=False)
        for saved_payload in (
            {"data": raw},
            {"data": encoded, "event": "order"},
            {"payload": {"data": encoded}},
            {"data": [raw]},
            {"_raw": encoded},
            {encoded: ""},
        ):
            with self.subTest(wrapper=list(saved_payload)[0][:30]):
                original = copy.deepcopy(saved_payload)
                summary = shop._submission_summary({"id": "old", "payload": saved_payload})
                self.assertEqual(summary["customer_name"], raw["fullname"])
                self.assertEqual(summary["customer_email"], raw["email"])
                self.assertEqual(summary["order_payload"]["payment"]["products"][0]["price"], "30")
                self.assertEqual(order_identity(saved_payload), raw["order_id"])
                self.assertEqual(order_number(saved_payload), "27")
                self.assertFalse(can_auto_update_cdek(saved_payload))
                self.assertEqual(saved_payload, original)
                self.assertEqual(summary["payload"], original)
        lead = {"Name": "Покупатель", "Email": "buyer@example.test", "data": {"email": "tracking@example.test"}}
        self.assertIs(normalize_order_payload(lead), lead)

    def test_taplink_prices_options_shipping_and_identity(self):
        raw = taplink_order()
        before = copy.deepcopy(raw)
        normalized = normalize_order_payload(raw)
        payment = normalized["payment"]
        self.assertEqual(raw, before)
        self.assertEqual(normalized["Name"], raw["fullname"])
        self.assertEqual(payment["amount"], "30")
        self.assertEqual(payment["subtotal"], "30")
        self.assertEqual(payment["delivery_price"], "0")
        self.assertEqual(payment["delivery_city"], "Тестовый город")
        self.assertEqual(payment["delivery_address"], "Тестовая улица, 1")
        self.assertEqual(payment["products"][0]["quantity"], "1")
        self.assertEqual(payment["products"][0]["price"], "30")
        self.assertEqual(payment["products"][0]["original_price"], "3000")
        self.assertEqual(payment["products"][0]["options"], [{"option": "Размер", "variant": "S"}])
        self.assertEqual(order_number(raw), "27")
        self.assertEqual(order_identity(raw), "27956212")
        self.assertEqual(storage._webhook_key(raw), "order:27956212")
        self.assertFalse(can_auto_update_cdek(raw))
        self.assertEqual(normalize_order_payload(normalized), normalized)

    def test_contact_and_shipping_fallbacks_and_form_encoded_structures(self):
        raw = taplink_order()
        for key in ("fullname", "email", "phone"):
            raw.pop(key)
        raw["records"].extend([
            {"title": "Email", "value": "fallback@example.test"},
            {"title": "Телефон", "value": "+79000000001"},
        ])
        raw["shipping_fields"] = "{broken JSON"
        for key in ("records", "offers", "records_extended"):
            raw[key] = json.dumps(raw[key], ensure_ascii=False)
        result = normalize_order_payload(raw)
        self.assertEqual(result["Name"], "Тестовый Покупатель")
        self.assertEqual(result["Email"], "fallback@example.test")
        self.assertEqual(result["Phone"], "+79000000001")
        self.assertEqual(result["payment"]["delivery_city"], "Резервный город")
        self.assertEqual(result["payment"]["products"][0]["price"], "30")

    def test_multiple_quantities_decimal_totals_and_zero_discount(self):
        raw = taplink_order()
        raw.pop("budget")
        raw["records_extended"] = []
        raw["shipping_price"] = "10,20"
        raw["offers"][0].update(amount="3", budget="0,30", price_discount="0,10")
        payment = normalize_order_payload(raw)["payment"]
        self.assertEqual(payment["products"][0]["line_total"], "0.30")
        self.assertEqual(payment["amount"], "10.50")
        raw["offers"][0].update(budget="0", price_discount="0")
        raw["budget"] = 0
        payment = normalize_order_payload(raw)["payment"]
        self.assertEqual(payment["amount"], "0")
        self.assertEqual(payment["products"][0]["price"], "0")
        self.assertEqual(payment["products"][0]["line_total"], "0")

    def test_missing_prices_and_invalid_nested_values_do_not_crash(self):
        raw = taplink_order()
        raw["offers"] = [None, "not a product", {"title": "Без скидки", "price": "120", "amount": "2"}]
        raw["shipping_fields"] = []
        product = normalize_order_payload(raw)["payment"]["products"][0]
        self.assertEqual(product["price"], "120")
        self.assertEqual(product["line_total"], "240")
        for number in ("NaN", "Infinity", "1e999999999", "1e-999999999", True):
            self.assertIsNone(decimal_value(number))

    def test_legacy_tilda_payload_and_numeric_zero_are_preserved(self):
        raw = tilda_order()
        self.assertIs(normalize_order_payload(raw), raw)
        request = shop._email_request_from_submission("submission", raw, "apexf1")
        self.assertEqual(request.order_id, "1301626769")
        self.assertEqual(request.order_sum, "6 088,77 RUB")
        self.assertEqual(request.delivery_sum, "688,77 RUB")
        self.assertTrue(can_auto_update_cdek(raw))
        raw["payment"].update(amount=0, delivery_price=0)
        request = shop._email_request_from_submission("submission", raw, "apexf1")
        self.assertEqual(request.order_sum, "0,00 RUB")
        self.assertEqual(request.delivery_sum, "0,00 RUB")

    def test_old_raw_submissions_get_normalized_without_a_migration(self):
        raw = taplink_order()
        summary = shop._submission_summary({"id": "old", "payload": raw, "im_number": raw["order_id"]})
        self.assertEqual(summary["customer_name"], raw["fullname"])
        self.assertEqual(summary["payload"], raw)
        self.assertEqual(summary["order_payload"]["payment"]["amount"], "30")
        self.assertEqual(summary["im_number"], "27")
        renamed = shop._submission_summary({"payload": raw, "im_number": "custom_27"})
        self.assertEqual(renamed["im_number"], "custom_27")

    def test_cdek_payload_builder_reads_both_formats_without_network_calls(self):
        with patch.object(tilda_cdek, "cdek_sender_location", return_value={"address": "Склад"}):
            for raw in (taplink_order(), tilda_order()):
                for webhook in (raw, {"payload": raw}):
                    result = tilda_cdek.build_cdek_order_payload(webhook, "test")
                    self.assertEqual(result["recipient"]["email"], "buyer@example.test")
                    self.assertEqual(result["recipient"]["name"], "Тестовый Покупатель")
                    self.assertEqual(result["recipient"]["phones"][0]["number"], "79000000000")
                    self.assertEqual(result["number"], order_number(raw))
                    item = result["packages"][0]["items"][0]
                    self.assertEqual(item["cost"], 30 if "offers" in raw else 5400)


class MemoryOrders:
    """Exercise HTTP/formatting paths without production database credentials."""
    def __init__(self):
        self.rows = {}
        self.keys = {}

    async def save(self, target, record):
        key = record["site"], storage._webhook_key(record["payload"])
        if key in self.keys:
            return self.keys[key], False
        self.keys[key] = record["id"]
        self.rows[record["id"]] = {**copy.deepcopy(record), "im_number": order_number(record["payload"]), "email_messages": []}
        return record["id"], True

    async def read(self, target, site):
        return copy.deepcopy([row for row in self.rows.values() if row["site"] == site])

    async def email(self, target, message):
        messages = self.rows[message["submission_id"]]["email_messages"]
        if not any(existing["id"] == message["id"] for existing in messages):
            messages.append(copy.deepcopy(message))
        return message

    async def update(self, target, site, submission_id, external_id, im_number):
        for row in self.rows.values():
            if row["site"] == site and (row["id"] == submission_id if submission_id else order_identity(row["payload"]) == external_id):
                row["im_number"] = im_number
                return True
        return False


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.orders = MemoryOrders()
        projects = {site: {"secret": "test-only", "database_target": "test-db", "cdek_account": "test"} for site in ("shelwear", "apexf1")}
        self.stack.enter_context(patch.object(shop.config, "TILDA_PROJECTS_JSON", json.dumps(projects)))
        for name, replacement in {
            "save_tilda_submission": self.orders.save, "read_tilda_submissions": self.orders.read,
            "save_tilda_email_message": self.orders.email, "update_tilda_submission_im_number": self.orders.update,
        }.items():
            self.stack.enter_context(patch.object(shop, name, replacement))
        self.smtp = self.stack.enter_context(patch.object(shop, "send_customer_email"))
        self.token = self.stack.enter_context(patch.object(shop, "cdek_get_token", return_value="test-token"))
        self.find = self.stack.enter_context(patch.object(shop, "find_cdek_order", return_value={"uuid": "test-uuid"}))
        self.rename = self.stack.enter_context(patch.object(shop, "update_cdek_order_number", return_value="request-uuid"))
        self.stack.enter_context(patch.object(shop, "_cdek_im_number_prefix", return_value="shop_"))
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.outbox = TildaOutbox(os.path.join(directory, "outbox.sqlite3"), shop._deliver_queued_submission)
        self.stack.enter_context(patch.object(shop, "_get_tilda_outbox", return_value=self.outbox))
        app = FastAPI()
        app.include_router(shop.router)
        self.client = self.stack.enter_context(TestClient(app))

    def submissions(self, site="shelwear"):
        response = self.client.get(f"/tilda/{site}/form/submissions?secret=test-only")
        self.assertEqual(response.status_code, 200)
        return response.json()["submissions"]

    def test_webhook_email_old_and_new_orders_and_retries(self):
        raw = taplink_order()
        response = self.client.post("/tilda/shelwear/webhook", json=raw)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["auto_actions"], {"email_sent": True, "im_number_updated": False})
        to, subject, body, body_html = self.smtp.call_args.args
        self.assertEqual(to, "buyer@example.test")
        self.assertIn("№27", subject)
        for text in (raw["fullname"], "30,00 RUB", "0,00 RUB", "размер: S", "Тестовый город"):
            self.assertIn(text, body)
        self.assertNotIn("цена: 3000", body)
        self.assertIn("Тестовая улица, 1", body_html)
        self.token.assert_not_called()
        duplicate = self.client.post("/tilda/shelwear/webhook", json=raw).json()
        self.assertTrue(duplicate["duplicate"])
        self.smtp.assert_called_once()
        raw["order_id"] = "another-technical-id"
        self.client.post("/tilda/shelwear/webhook", json=raw)
        self.assertEqual(len(self.submissions()), 2)
        self.assertEqual(self.smtp.call_count, 2)
        self.assertEqual(self.client.get("/tilda/shelwear/form/submissions?secret=wrong").status_code, 403)

    def test_urlencoded_webhook_and_manual_notification_defaults(self):
        raw = taplink_order()
        encoded = {key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in raw.items()}
        self.assertEqual(self.client.post("/tilda/shelwear/webhook", data=encoded).status_code, 200)
        submission = self.submissions()[0]
        response = self.client.post("/tilda/shelwear/form/email/send?secret=test-only", json={
            "submission_id": submission["id"], "message_type": "order_notification", "to_email": "buyer@example.test",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.submissions()[0]["email_messages"]), 2)
        _, subject, body, _ = self.smtp.call_args.args
        self.assertIn("№27", subject)
        self.assertIn("Сумма заказа: 30,00 RUB", body)
        self.assertIn("размер: S", body)

    def test_wrapped_form_webhook_exposes_email_and_can_send_a_notification(self):
        raw = taplink_order()
        response = self.client.post("/tilda/shelwear/webhook", data={
            "event": "order", "data": json.dumps(raw, ensure_ascii=False),
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["auto_actions"]["email_sent"])
        submission = self.submissions()[0]
        self.assertEqual(submission["customer_email"], raw["email"])
        self.assertEqual(submission["order_payload"]["payment"]["products"][0]["price"], "30")
        response = self.client.post("/tilda/shelwear/form/email/send?secret=test-only", json={
            "submission_id": submission["id"], "message_type": "order_notification", "to_email": raw["email"],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.smtp.call_count, 2)
        self.assertIn("№27", self.smtp.call_args.args[1])

    def test_json_body_with_a_form_content_type_is_not_saved_as_a_form_key(self):
        raw = taplink_order()
        for index, content_type in enumerate(("application/x-www-form-urlencoded", "text/plain", "")):
            with self.subTest(content_type=content_type):
                body = {**raw, "order_id": f"mislabeled-{index}"}
                response = self.client.post(
                    "/tilda/shelwear/webhook", content=json.dumps(body, ensure_ascii=False).encode(),
                    headers={"Content-Type": content_type},
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["auto_actions"]["email_sent"])
                rows = self.submissions()
                self.assertEqual(next(row for row in rows if row["id"] == response.json()["submission_id"])["payload"], body)

    def test_dashboard_assets_are_versioned_and_responses_are_not_cached(self):
        response = self.client.get("/tilda/shelwear/form?secret=test-only")
        self.assertEqual(response.status_code, 200)
        self.assertRegex(response.text, r'/static/js/tilda/form\.js\?v=[a-f0-9]+')
        self.assertIn("no-store", response.headers["cache-control"])

    def test_database_outage_is_accepted_locally_and_replayed_without_a_new_webhook(self):
        from psycopg_pool import PoolTimeout

        with patch.object(shop, "save_tilda_submission", new_callable=AsyncMock,
                          side_effect=PoolTimeout("database unavailable")):
            response = self.client.post("/tilda/shelwear/webhook", json=taplink_order())
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["queued"])
            self.assertEqual(response.json()["storage"], "local")
            duplicate = self.client.post("/tilda/shelwear/webhook", json=taplink_order())
            self.assertTrue(duplicate.json()["duplicate"])
            self.assertEqual(duplicate.json()["submission_id"], response.json()["submission_id"])
            with patch.object(shop, "read_tilda_submissions", new_callable=AsyncMock,
                              side_effect=PoolTimeout("database unavailable")):
                state = self.client.get("/tilda/shelwear/form/submissions?secret=test-only").json()
            self.assertFalse(state["database_available"])
            self.assertEqual(state["pending_count"], 1)
            self.assertEqual(state["submissions"][0]["storage_status"], "queued")
            self.assertEqual(state["submissions"][0]["payload"], taplink_order())
            self.smtp.assert_not_called()
        self.outbox.blocked_until.clear()
        with self.outbox._connect() as conn:
            conn.execute("UPDATE webhook_outbox SET retry_at=0")
        self.client.portal.call(self.outbox.run_once)
        state = self.client.get("/tilda/shelwear/form/submissions?secret=test-only").json()
        self.assertTrue(state["database_available"])
        self.assertEqual(state["pending_count"], 0)
        self.assertEqual(len(state["submissions"]), 1)
        self.smtp.assert_called_once()

    def test_disk_failure_returns_retryable_error_without_acknowledging_or_sending(self):
        with patch.object(self.outbox, "_enqueue", side_effect=sqlite3.OperationalError("disk is full")):
            response = self.client.post("/tilda/shelwear/webhook", json=taplink_order())
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(response.headers["retry-after"], "60")
        self.smtp.assert_not_called()
        self.assertEqual(self.orders.rows, {})

    def test_read_pool_timeout_returns_queue_status_instead_of_http_500(self):
        from psycopg_pool import PoolTimeout

        with patch.object(shop, "read_tilda_submissions", new_callable=AsyncMock,
                          side_effect=PoolTimeout("database unavailable")):
            response = self.client.get("/tilda/shelwear/form/submissions?secret=test-only")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["database_available"])
        self.assertEqual(response.json()["pending_count"], 0)

    def test_pending_order_cannot_send_a_second_notification_manually(self):
        from psycopg_pool import PoolTimeout

        with patch.object(shop, "save_tilda_submission", new_callable=AsyncMock,
                          side_effect=PoolTimeout("database unavailable")):
            response = self.client.post("/tilda/shelwear/webhook", json=taplink_order()).json()
        manual = self.client.post("/tilda/shelwear/form/email/send?secret=test-only", json={
            "submission_id": response["submission_id"], "message_type": "order_notification", "to_email": "buyer@example.test",
        })
        self.assertEqual(manual.status_code, 503)
        self.smtp.assert_not_called()
        response = self.client.get("/tilda/shelwear/form/submissions?secret=test-only")
        self.assertIn("no-store", response.headers["cache-control"])

    def test_taplink_cdek_automatic_and_manual_number_changes(self):
        raw = taplink_order()
        raw["shipping_fields"] = {"shipping_method": "Доставка в ПВЗ СДЭК", "city": "Город"}
        response = self.client.post("/tilda/shelwear/webhook", json=raw).json()
        self.assertEqual(response["auto_actions"]["new_im_number"], "shop_27")
        self.find.assert_called_once_with("27", "test-token", by="im_number")
        submission = self.submissions()[0]
        self.assertEqual(submission["im_number"], "shop_27")
        response = self.client.post("/tilda/shelwear/form/cdek/im-number?secret=test-only", json={
            "submission_id": submission["id"], "order_id": raw["order_id"],
            "current_im_number": "shop_27", "new_im_number": "shelwear_27",
        })
        self.assertEqual(response.status_code, 200)
        self.find.assert_called_with("shop_27", "test-token", by="im_number")
        self.assertEqual(self.submissions()[0]["im_number"], "shelwear_27")
        self.assertEqual(self.submissions()[0]["payload"], raw)

    def test_legacy_tilda_webhook_still_sends_and_renumbers(self):
        raw = tilda_order()
        response = self.client.post("/tilda/apexf1/webhook", json=raw)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["auto_actions"]["email_sent"])
        self.find.assert_called_once_with("1301626769", "test-token", by="im_number")
        self.assertEqual(self.submissions("apexf1")[0]["im_number"], "shop_1301626769")
        _, _, body, _ = self.smtp.call_args.args
        self.assertIn("6 088,77 RUB", body)
        self.assertIn("размер: L", body)

    def test_shop_names_reach_smtp_for_webhooks_and_both_manual_message_types(self):
        projects = json.loads(shop.config.TILDA_PROJECTS_JSON)
        projects["real_baby"] = {"secret": "test-only", "database_target": "test-db", "cdek_account": "test"}
        with patch.object(shop.config, "TILDA_PROJECTS_JSON", json.dumps(projects)), \
                patch.object(shop, "send_customer_email", tilda_cdek.send_customer_email), \
                patch.object(tilda_cdek.smtplib, "SMTP") as smtp:
            server = smtp.return_value.__enter__.return_value

            def assert_sender(expected):
                message = server.send_message.call_args.args[0]
                parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
                address, = parsed["From"].addresses
                self.assertEqual(address.display_name, expected)
                self.assertEqual(address.addr_spec, tilda_cdek.SMTP_FROM)
                self.assertEqual(server.send_message.call_args.kwargs["from_addr"], tilda_cdek.SMTP_FROM)

            for site, expected in (("apexf1", "APEX"), ("shelwear", "SHELWEAR"), ("real_baby", "REAL BABY")):
                # The request body cannot choose the sender's shop name or mailbox.
                raw = {**taplink_order(), "sender_name": "Other shop", "From": "other@example.test"}
                response = self.client.post(f"/tilda/{site}/webhook", json=raw)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["auto_actions"]["email_sent"])
                assert_sender(expected)
                submission_id = response.json()["submission_id"]
                for message_type in ("order_notification", "custom_message"):
                    response = self.client.post(f"/tilda/{site}/form/email/send?secret=test-only", json={
                        "submission_id": submission_id,
                        "message_type": message_type, "to_email": raw["email"],
                        "custom_subject": "Вопрос по заказу", "custom_body": "Здравствуйте!",
                    })
                    self.assertEqual(response.status_code, 200, response.text)
                    assert_sender(expected)
            self.assertEqual(server.send_message.call_count, 9)


class MailHeaderTests(unittest.TestCase):
    def test_unicode_sender_name_is_encoded_as_one_mailbox(self):
        with patch.object(tilda_cdek.smtplib, "SMTP") as smtp:
            tilda_cdek.send_customer_email("buyer@example.test", "Тема", "Текст", sender_name='Магазин "Дом", одежда')
            server = smtp.return_value.__enter__.return_value
            message = server.send_message.call_args.args[0]
            parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
            address, = parsed["From"].addresses
            self.assertEqual(address.display_name, 'Магазин "Дом", одежда')
            self.assertEqual(address.addr_spec, tilda_cdek.SMTP_FROM)
            self.assertEqual(server.send_message.call_args.kwargs["from_addr"], tilda_cdek.SMTP_FROM)
            self.assertEqual(parsed.get_content(), "Текст")

    def test_sender_name_cannot_inject_headers(self):
        with patch.object(tilda_cdek.smtplib, "SMTP") as smtp:
            with self.assertRaises(ValueError):
                tilda_cdek.send_customer_email("buyer@example.test", "Тема", "Текст", sender_name="Shop\r\nBcc: other@example.test")
            smtp.assert_not_called()


@unittest.skipUnless(os.environ.get("TEST_TILDA_DATABASE_DSN"), "Set TEST_TILDA_DATABASE_DSN for isolated PostgreSQL checks")
class DatabaseTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg_pool import ConnectionPool

        dsn = os.environ["TEST_TILDA_DATABASE_DSN"]
        schema = "test_tilda_" + uuid.uuid4().hex
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

        def drop_schema():
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

        self.addCleanup(drop_schema)
        pool = ConnectionPool(dsn, min_size=1, max_size=2, kwargs={"options": f"-c search_path={schema}"})
        self.addCleanup(pool.close)
        for module in (schemas, storage):
            mocked = patch.object(module, "get_pool", return_value=pool)
            mocked.start()
            self.addCleanup(mocked.stop)
        schemas._create_tilda_submissions_schema("test-db")

    def save(self, submission_id, payload, site="shelwear"):
        return storage._save_tilda_submission("test-db", {
            "id": submission_id, "site": site, "created_at": datetime.now(timezone.utc).isoformat(),
            "payload_type": "json", "payload": payload,
        })

    def test_sql_preserves_raw_payload_deduplicates_and_reads_email_history(self):
        raw = taplink_order()
        self.assertEqual(self.save("one", raw), ("one", True))
        self.assertEqual(self.save("retry", raw), ("one", False))
        self.assertEqual(self.save("wrapped-retry", {"data": json.dumps(raw)}), ("one", False))
        raw2 = {**raw, "order_id": "different-id"}
        self.assertEqual(self.save("two", raw2), ("two", True))
        message = storage._save_tilda_email_message("test-db", {
            "id": "mail", "submission_id": "one", "site": "shelwear",
            "created_at": datetime.now(timezone.utc).isoformat(), "message_type": "order_notification",
            "to_email": "buyer@example.test", "subject": "test", "body": "test", "body_html": "test", "status": "sent",
        })
        storage._save_tilda_email_message("test-db", {**message, "body_html": "test"})
        rows = {row["id"]: row for row in storage._read_tilda_submissions("test-db", "shelwear", None)}
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows["one"]["payload"], raw)
        self.assertEqual(rows["one"]["im_number"], "27")
        self.assertEqual(rows["one"]["email_messages"][0]["id"], "mail")

    def test_sql_number_update_only_changes_selected_submission_in_its_site(self):
        raw = taplink_order()
        self.save("one", raw)
        self.save("two", {**raw, "order_id": "other-id"})
        self.save("another-site", raw, site="another")
        # An ID in the request must take precedence over any matching order_id.
        self.assertTrue(storage._update_tilda_submission_im_number("test-db", "shelwear", "one", "other-id", "renamed"))
        rows = {row["id"]: row for row in storage._read_tilda_submissions("test-db", "shelwear", None)}
        self.assertEqual(rows["one"]["im_number"], "renamed")
        self.assertEqual(rows["two"]["im_number"], "27")
        self.assertFalse(storage._update_tilda_submission_im_number("test-db", "shelwear", "missing", "other-id", "wrong"))
        self.assertTrue(storage._update_tilda_submission_im_number("test-db", "shelwear", "", "other-id", "by-external-id"))
        rows = {row["id"]: row for row in storage._read_tilda_submissions("test-db", "shelwear", None)}
        self.assertEqual(rows["two"]["im_number"], "by-external-id")
        self.assertEqual(storage._read_tilda_submissions("test-db", "another", None)[0]["im_number"], "27")

    def test_local_queue_replays_into_real_postgresql_with_email_history(self):
        import asyncio
        from pathlib import Path
        from psycopg_pool import PoolTimeout

        async def check(path):
            box = TildaOutbox(path, shop._deliver_queued_submission)
            await box.initialize()
            raw = taplink_order()
            record = {"id": "queued-order", "site": "shelwear", "payload_type": "json", "payload": raw,
                      "created_at": datetime.now(timezone.utc).isoformat()}
            with patch.object(storage, "get_pool", side_effect=PoolTimeout("offline")):
                item_id, _ = await box.enqueue("test-db", record)
                self.assertTrue((await box.process(item_id))["queued"])
            box.blocked_until.clear()
            with box._connect() as conn:
                conn.execute("UPDATE webhook_outbox SET retry_at=0")
            await box.run_once()
            self.assertEqual(await box.pending_submissions("test-db", "shelwear"), [])
            rows = storage._read_tilda_submissions("test-db", "shelwear", None)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["id"], "queued-order")
            self.assertEqual(rows[0]["payload"], raw)
            self.assertEqual(len(rows[0]["email_messages"]), 1)

        with tempfile.TemporaryDirectory() as directory, patch.object(shop, "send_customer_email") as smtp:
            asyncio.run(check(Path(directory) / "outbox.sqlite3"))
            smtp.assert_called_once()


if __name__ == "__main__":
    unittest.main()
