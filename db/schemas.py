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
            im_number TEXT NOT NULL DEFAULT ''
        );
        
        ALTER TABLE {table}
            ADD COLUMN IF NOT EXISTS im_number TEXT NOT NULL DEFAULT '';

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
        email_submission_created_idx=sql.Identifier(
            f"{TILDA_EMAIL_MESSAGES_TABLE}_submission_created_at_idx"
        ),
    )

    with get_pool(database_name).connection() as conn:
        conn.execute(query)


async def create_tilda_submissions_schema(database_name: str) -> None:
    """Create storage for Tilda webhook submissions in a project database."""

    await asyncio.to_thread(_create_tilda_submissions_schema, database_name)