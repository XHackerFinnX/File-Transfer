import asyncio
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from db.connections import get_pool
from db.schemas import TILDA_SUBMISSIONS_TABLE


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


def _save_tilda_submission(database_name: str, submission: dict[str, Any]) -> None:
    query = f"""
        INSERT INTO {TILDA_SUBMISSIONS_TABLE} (
            id, site, created_at, payload_type, payload, cookies, client, headers
        ) VALUES (
            %(id)s, %(site)s, %(created_at)s, %(payload_type)s,
            %(payload)s::jsonb, %(cookies)s::jsonb, %(client)s::jsonb, %(headers)s::jsonb
        )
    """
    params = {
        **submission,
        "payload": Jsonb(submission.get("payload", {})),
        "cookies": Jsonb(submission.get("cookies", {})),
        "client": Jsonb(submission.get("client", {})),
        "headers": Jsonb(submission.get("headers", {})),
    }
    
    def execute_insert(conn):
        conn.execute(query, params)
        
    _with_database_retry(database_name, execute_insert)


async def save_tilda_submission(database_name: str, submission: dict[str, Any]) -> None:
    await asyncio.to_thread(_save_tilda_submission, database_name, submission)


def _read_tilda_submissions(
    database_name: str,
    site: str,
    limit: int,
) -> list[dict[str, Any]]:
    query = f"""
        SELECT id, site, created_at, payload_type, payload, cookies, client, headers
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
        submissions.append(dict(row))
    return submissions


async def read_tilda_submissions(
    database_name: str,
    site: str,
    limit: int,
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_read_tilda_submissions, database_name, site, limit)