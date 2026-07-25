import asyncio

from psycopg import sql

from db.connections import get_pool

TILDA_SUBMISSIONS_TABLE = "tilda_submissions"
TILDA_EMAIL_MESSAGES_TABLE = "tilda_email_messages"


def _create_tilda_submissions_schema(database_name: str) -> None:
    query = sql.SQL("""
        CREATE TABLE IF NOT EXISTS {table} (
            id TEXT PRIMARY KEY,
            site TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            payload_type TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            cookies JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            client JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            headers JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            im_number TEXT NOT NULL DEFAULT '',
            webhook_key TEXT NOT NULL DEFAULT ''
        );
        
        ALTER TABLE {table}
            ADD COLUMN IF NOT EXISTS im_number TEXT NOT NULL DEFAULT '';
            
        ALTER TABLE {table}
            ADD COLUMN IF NOT EXISTS webhook_key TEXT NOT NULL DEFAULT '';

        WITH existing_orders AS (
            SELECT DISTINCT ON (site, order_id) id, order_id
            FROM (
                SELECT
                    id,
                    site,
                    created_at,
                    COALESCE(
                        payload #>> '{{payment,orderid}}',
                        payload #>> '{{payment,order_id}}',
                        payload #>> '{{Payment,orderid}}',
                        payload #>> '{{Payment,order_id}}',
                        payload ->> 'orderid',
                        payload ->> 'order_id',
                        payload ->> 'Order ID',
                        payload ->> 'Номер заказа'
                    ) AS order_id
                FROM {table}
            ) submissions
            WHERE order_id IS NOT NULL AND order_id <> ''
            ORDER BY site, order_id, created_at ASC
        )
        UPDATE {table} submissions
        SET webhook_key = 'order:' || existing_orders.order_id
        FROM existing_orders
        WHERE submissions.id = existing_orders.id
          AND submissions.webhook_key = '';

        CREATE UNIQUE INDEX IF NOT EXISTS {webhook_key_idx}
            ON {table} (site, webhook_key)
            WHERE webhook_key <> '';

        CREATE INDEX IF NOT EXISTS {site_created_idx}
            ON {table} (site, created_at DESC);
            
        CREATE TABLE IF NOT EXISTS {email_table} (
            id TEXT PRIMARY KEY,
            submission_id TEXT NOT NULL REFERENCES {table} (id) ON DELETE CASCADE,
            site TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            message_type TEXT NOT NULL,
            to_email TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            body_html TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'sent'
        );

        CREATE INDEX IF NOT EXISTS {email_submission_created_idx}
            ON {email_table} (submission_id, created_at DESC);
        """).format(
        table=sql.Identifier(TILDA_SUBMISSIONS_TABLE),
        email_table=sql.Identifier(TILDA_EMAIL_MESSAGES_TABLE),
        site_created_idx=sql.Identifier(
            f"{TILDA_SUBMISSIONS_TABLE}_site_created_at_idx"
        ),
        webhook_key_idx=sql.Identifier(
            f"{TILDA_SUBMISSIONS_TABLE}_site_webhook_key_idx"
        ),
        email_submission_created_idx=sql.Identifier(
            f"{TILDA_EMAIL_MESSAGES_TABLE}_submission_created_at_idx"
        ),
    )

    with get_pool(database_name).connection() as conn:
        conn.execute(query)


async def create_tilda_submissions_schema(database_name: str) -> None:
    """Create storage for Tilda webhook submissions in a project database."""

    await asyncio.to_thread(_create_tilda_submissions_schema, database_name)