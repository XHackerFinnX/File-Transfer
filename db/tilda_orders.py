import asyncio
import hashlib
import json
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from db.connections import get_pool
from db.schemas import TILDA_EMAIL_MESSAGES_TABLE, TILDA_SUBMISSIONS_TABLE


def _extract_order_id(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""

    payment = (
        payload.get("payment") or payload.get("Payment") or payload.get("Оплата") or {}
    )
    if not isinstance(payment, dict):
        payment = {}
    value = (
        payment.get("orderid")
        or payment.get("order_id")
        or payload.get("orderid")
        or payload.get("order_id")
        or payload.get("Order ID")
        or payload.get("Номер заказа")
        or ""
    )
    return str(value).strip()


def _webhook_key(payload: dict[str, Any]) -> str:
    """Return a stable identity for retries of the same Tilda webhook."""
    order_id = _extract_order_id(payload)
    if order_id:
        return f"order:{order_id}"
    canonical_payload = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return f"payload:{hashlib.sha256(canonical_payload.encode('utf-8')).hexdigest()}"


def _with_database_retry(database_name: str, operation):
    """Run a database operation and retry once if the pooled connection is stale."""
    pool = get_pool(database_name)
    try:
        with pool.connection() as conn:
            return operation(conn)
    except (psycopg.OperationalError, psycopg.InterfaceError):
        pool.check()
        with pool.connection() as conn:
            return operation(conn)


def _save_tilda_submission(
    database_name: str, submission: dict[str, Any]
) -> tuple[str, bool]:
    query = f"""
        INSERT INTO {TILDA_SUBMISSIONS_TABLE} (
            id, site, created_at, payload_type, payload, cookies, client, headers,
            im_number, webhook_key
        ) VALUES (
            %(id)s, %(site)s, %(created_at)s, %(payload_type)s,
            %(payload)s::jsonb, %(cookies)s::jsonb, %(client)s::jsonb, %(headers)s::jsonb,
            %(im_number)s, %(webhook_key)s
        )
        ON CONFLICT (site, webhook_key) WHERE webhook_key <> '' DO NOTHING
        RETURNING id
    """
    params = {
        **submission,
        "payload": Jsonb(submission.get("payload", {})),
        "cookies": Jsonb(submission.get("cookies", {})),
        "client": Jsonb(submission.get("client", {})),
        "headers": Jsonb(submission.get("headers", {})),
        "im_number": _extract_order_id(submission.get("payload", {})),
        "webhook_key": _webhook_key(submission.get("payload", {})),
    }
    
    def execute_insert(conn):
        inserted = conn.execute(query, params).fetchone()
        if inserted:
            return str(inserted[0]), True
        existing = conn.execute(
            f"""
            SELECT id FROM {TILDA_SUBMISSIONS_TABLE}
            WHERE site = %(site)s AND webhook_key = %(webhook_key)s
            """,
            params,
        ).fetchone()
        return str(existing[0]), False
        
    return _with_database_retry(database_name, execute_insert)


async def save_tilda_submission(
    database_name: str, submission: dict[str, Any]
) -> tuple[str, bool]:
    return await asyncio.to_thread(_save_tilda_submission, database_name, submission)


def _read_tilda_submissions(
    database_name: str,
    site: str,
    limit: int,
) -> list[dict[str, Any]]:
    query = f"""
        SELECT
            id,
            site,
            created_at,
            payload_type,
            payload,
            cookies,
            client,
            headers,
            im_number,
            COALESCE(
                (
                    SELECT jsonb_agg(
                        jsonb_build_object(
                            'id', messages.id,
                            'created_at', messages.created_at,
                            'message_type', messages.message_type,
                            'to_email', messages.to_email,
                            'subject', messages.subject,
                            'body', messages.body,
                            'status', messages.status
                        )
                        ORDER BY messages.created_at DESC
                    )
                    FROM {TILDA_EMAIL_MESSAGES_TABLE} AS messages
                    WHERE messages.submission_id = {TILDA_SUBMISSIONS_TABLE}.id
                ),
                '[]'::jsonb
            ) AS email_messages
        FROM {TILDA_SUBMISSIONS_TABLE}
        WHERE site = %(site)s
        ORDER BY created_at DESC
        LIMIT %(limit)s
    """
    
    def fetch_rows(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, {"site": site, "limit": limit})
            return cur.fetchall()
        
    rows = _with_database_retry(database_name, fetch_rows)

    submissions: list[dict[str, Any]] = []
    for row in rows:
        created_at = row.get("created_at")
        if isinstance(created_at, datetime):
            row["created_at"] = created_at.isoformat()
        email_messages = row.get("email_messages") or []
        for message in email_messages:
            message_created_at = message.get("created_at")
            if isinstance(message_created_at, datetime):
                message["created_at"] = message_created_at.isoformat()
        submissions.append(dict(row))
    return submissions


async def read_tilda_submissions(
    database_name: str,
    site: str,
    limit: int,
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_read_tilda_submissions, database_name, site, limit)


def _update_tilda_submission_im_number(
    database_name: str,
    site: str,
    submission_id: str,
    order_id: str,
    im_number: str,
) -> bool:
    query = f"""
        UPDATE {TILDA_SUBMISSIONS_TABLE}
        SET im_number = %(im_number)s
        WHERE site = %(site)s
          AND (
              (%(submission_id)s <> '' AND id = %(submission_id)s)
              OR (payload #>> '{{payment,orderid}}' = %(order_id)s)
              OR (payload #>> '{{payment,order_id}}' = %(order_id)s)
              OR (payload ->> 'orderid' = %(order_id)s)
              OR (payload ->> 'order_id' = %(order_id)s)
              OR (payload ->> 'Order ID' = %(order_id)s)
              OR (payload ->> 'Номер заказа' = %(order_id)s)
          )
    """

    def execute_update(conn):
        result = conn.execute(
            query,
            {
                "site": site,
                "submission_id": submission_id,
                "order_id": order_id,
                "im_number": im_number,
            },
        )
        return bool(result.rowcount)

    return _with_database_retry(database_name, execute_update)


async def update_tilda_submission_im_number(
    database_name: str,
    site: str,
    submission_id: str,
    order_id: str,
    im_number: str,
) -> bool:
    return await asyncio.to_thread(
        _update_tilda_submission_im_number,
        database_name,
        site,
        submission_id,
        order_id,
        im_number,
    )
    
def _save_tilda_email_message(
    database_name: str, message: dict[str, Any]
) -> dict[str, Any]:
    query = f"""
        INSERT INTO {TILDA_EMAIL_MESSAGES_TABLE} (
            id, submission_id, site, created_at, message_type, to_email, subject, body, body_html, status
        ) VALUES (
            %(id)s, %(submission_id)s, %(site)s, %(created_at)s, %(message_type)s,
            %(to_email)s, %(subject)s, %(body)s, %(body_html)s, %(status)s
        )
        RETURNING id, submission_id, site, created_at, message_type, to_email, subject, body, status
    """

    def execute_insert(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, message)
            return cur.fetchone()

    row = _with_database_retry(database_name, execute_insert)
    if isinstance(row.get("created_at"), datetime):
        row["created_at"] = row["created_at"].isoformat()
    return dict(row)


async def save_tilda_email_message(
    database_name: str, message: dict[str, Any]
) -> dict[str, Any]:
    return await asyncio.to_thread(_save_tilda_email_message, database_name, message)