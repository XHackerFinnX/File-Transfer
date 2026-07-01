import asyncio

from psycopg import sql

from db.connections import get_pool

TILDA_SUBMISSIONS_TABLE = "tilda_submissions"


def _create_tilda_submissions_schema(database_name: str) -> None:
    query = sql.SQL(
        """
        CREATE TABLE IF NOT EXISTS {table} (
            id TEXT PRIMARY KEY,
            site TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            payload_type TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            cookies JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            client JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            headers JSONB NOT NULL DEFAULT '{{}}'::jsonb
        );

        CREATE INDEX IF NOT EXISTS {site_created_idx}
            ON {table} (site, created_at DESC);
        """
    ).format(
        table=sql.Identifier(TILDA_SUBMISSIONS_TABLE),
        site_created_idx=sql.Identifier(
            f"{TILDA_SUBMISSIONS_TABLE}_site_created_at_idx"
        ),
    )

    with get_pool(database_name).connection() as conn:
        conn.execute(query)


async def create_tilda_submissions_schema(database_name: str) -> None:
    """Create storage for Tilda webhook submissions in a project database."""

    await asyncio.to_thread(_create_tilda_submissions_schema, database_name)