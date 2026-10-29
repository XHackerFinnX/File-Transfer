"""Targeted pool recovery; healthy/busy pools must not be reset for overload."""

import asyncio
import os
import tempfile
import unittest
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from tests.test_tilda_webhooks import TEST_ENV  # Import isolated test settings first.
from db import connections
from services.tilda_outbox import TildaOutbox


class PoolRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.target = connections.DatabaseTarget("recovery-test", "unused_test_database")
        self.pool = Mock(closed=False, max_size=4)
        self.pool.get_stats.return_value = {"pool_available": 0, "pool_size": 1, "connections_errors": 4}
        self.registry = {self.target.name: self.pool}
        self.targets = patch.object(connections, "DATABASE_TARGETS", {self.target.name: self.target})
        self.pools = patch.object(connections, "_pools", self.registry)
        self.targets.start()
        self.pools.start()
        self.addCleanup(self.targets.stop)
        self.addCleanup(self.pools.stop)

    def test_stuck_reconnecting_pool_is_replaced_with_tcp_liveness_settings(self):
        self.assertTrue(connections.recover_database_pool(self.target.name))
        self.pool.close.assert_called_once()
        self.assertNotIn(self.target.name, self.registry)
        with patch.object(connections, "ConnectionPool") as factory:
            factory.return_value.closed = False
            fresh = connections.get_pool(self.target.name)
            self.assertIsNot(fresh, self.pool)
            self.assertIs(connections.get_pool(self.target.name), fresh)
            self.assertEqual(factory.call_count, 1)
            options = factory.call_args.kwargs
            self.assertEqual(options["reconnect_timeout"], connections.config.POSTGRESQL_RECONNECT_TIMEOUT_SECONDS)
            self.assertGreater(options["kwargs"]["tcp_user_timeout"], 0)
            self.assertGreater(options["kwargs"]["keepalives_count"], 0)

    def test_healthy_or_busy_pool_is_not_reset_for_request_timeouts(self):
        for stats in (
            {"pool_available": 1, "pool_size": 1, "connections_errors": 4},
            {"pool_available": 0, "pool_size": 4, "connections_errors": 4},
            {"pool_available": 0, "pool_size": 1, "connections_errors": 0},
        ):
            self.pool.get_stats.return_value = stats
            self.assertFalse(connections.recover_database_pool(self.target.name))
            self.assertIs(self.registry[self.target.name], self.pool)
        self.pool.close.assert_not_called()

    def test_closed_pool_is_retired_without_reading_stats(self):
        self.pool.closed = True
        self.assertTrue(connections.recover_database_pool(self.target.name))
        self.pool.get_stats.assert_not_called()


@unittest.skipUnless(os.environ.get("TEST_TILDA_DATABASE_DSN"), "Set TEST_TILDA_DATABASE_DSN for local PostgreSQL recovery check")
class LivePoolRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_outage_and_tcp_disconnect_recover_without_redeploy_or_new_webhook(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict
        from pydantic import SecretStr

        params = conninfo_to_dict(os.environ["TEST_TILDA_DATABASE_DSN"])
        schema = "test_reconnect_" + uuid.uuid4().hex
        available = True
        writers, handlers = set(), set()

        async def forward(reader, writer):
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()

        async def proxy(reader, writer):
            task = asyncio.current_task()
            handlers.add(task)
            upstream = None
            transfers = []
            writers.add(writer)
            try:
                if not available:
                    return
                upstream_reader, upstream = await asyncio.open_connection(params.get("host", "127.0.0.1"), int(params.get("port", 5432)))
                writers.add(upstream)
                transfers = [asyncio.create_task(forward(reader, upstream)), asyncio.create_task(forward(upstream_reader, writer))]
                await asyncio.wait(transfers, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for transfer in transfers:
                    transfer.cancel()
                await asyncio.gather(*transfers, return_exceptions=True)
                for stream in (writer, upstream):
                    if stream:
                        stream.close()
                        writers.discard(stream)
                handlers.discard(task)

        async def wait_until(predicate):
            async with asyncio.timeout(5):
                while not predicate():
                    await asyncio.sleep(0.01)

        with psycopg.connect(os.environ["TEST_TILDA_DATABASE_DSN"], autocommit=True) as admin, \
                tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            admin.execute(sql.SQL("CREATE SCHEMA {}; CREATE TABLE {}.orders (id TEXT PRIMARY KEY)").format(
                sql.Identifier(schema), sql.Identifier(schema)))
            server = await asyncio.start_server(proxy, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            target = connections.DatabaseTarget("network-recovery-test", params["dbname"])
            stack.enter_context(patch.object(connections, "DATABASE_TARGETS", {target.name: target}))
            stack.enter_context(patch.object(connections, "_pools", {}))
            for key, value in {
                "POSTGRESQL_HOST": f"127.0.0.1:{port}", "POSTGRESQL_USER": params["user"],
                "POSTGRESQL_PASSWORD": SecretStr(params["password"]), "POSTGRESQL_SSLMODE": "disable",
                "POSTGRESQL_POOL_TIMEOUT_SECONDS": 0.15, "POSTGRESQL_CONNECT_TIMEOUT_SECONDS": 1,
            }.items():
                stack.enter_context(patch.object(connections.config, key, value))

            def probe():
                with connections.get_pool(target.name).connection() as conn:
                    conn.execute("SELECT 1")

            async def prepare(name):
                await asyncio.to_thread(probe)

            async def recover(name):
                await asyncio.to_thread(connections.recover_database_pool, name)

            async def deliver(item):
                def insert():
                    with connections.get_pool(target.name).connection() as conn:
                        conn.execute(sql.SQL("INSERT INTO {}.orders VALUES (%s) ON CONFLICT DO NOTHING").format(
                            sql.Identifier(schema)), (item.id,))
                await asyncio.to_thread(insert)
                return {"ok": True}

            box = TildaOutbox(Path(directory) / "queue.sqlite3", deliver, prepare=prepare, recover=recover, retry_seconds=0.15)
            try:
                await asyncio.to_thread(probe)
                original_pool = connections.get_pool(target.name)
                available = False
                for stream in tuple(writers):
                    stream.close()
                await box.initialize()
                await box.enqueue(target.name, {"id": "queued-order", "site": "shelwear", "payload": {"order_id": "42"}})
                await box.start([target.name])
                await wait_until(lambda: original_pool.get_stats().get("connections_errors", 0) > 0)
                self.assertNotEqual(box._row("queued-order")["status"], "done")
                available = True
                await wait_until(lambda: box._row("queued-order")["status"] == "done")
                self.assertTrue(original_pool.closed)
                self.assertIsNot(connections.get_pool(target.name), original_pool)
                count = admin.execute(sql.SQL("SELECT count(*) FROM {}.orders").format(sql.Identifier(schema))).fetchone()[0]
                self.assertEqual(count, 1)
            finally:
                await box.stop()
                await asyncio.to_thread(connections.close_database_pools)
                server.close()
                await server.wait_closed()
                for stream in tuple(writers):
                    stream.close()
                for handler in tuple(handlers):
                    handler.cancel()
                await asyncio.gather(*tuple(handlers), return_exceptions=True)
                admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


if __name__ == "__main__":
    unittest.main()
