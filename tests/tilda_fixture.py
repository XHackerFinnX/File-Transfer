"""Local browser fixture; never mounts this module in the production app.

All storage, SMTP and CDEK calls use in-memory test doubles.
"""
import json
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from tests.test_tilda_webhooks import MemoryOrders, shop, taplink_order
from services.tilda_outbox import TildaOutbox
from psycopg_pool import PoolTimeout


def create_app():
    orders = MemoryOrders()
    local_dir = tempfile.TemporaryDirectory()
    outbox = TildaOutbox(Path(local_dir.name) / "outbox.sqlite3", shop._deliver_queued_submission)
    shop._get_tilda_outbox = lambda: outbox
    database_up = True

    async def save(target, record):
        if not database_up:
            raise PoolTimeout("test outage")
        return await orders.save(target, record)

    async def read(target, site):
        if not database_up:
            raise PoolTimeout("test outage")
        return await orders.read(target, site)
    shop.config.TILDA_PROJECTS_JSON = json.dumps({
        site: {"secret": "test-only", "database_target": "test-db", "cdek_account": "test"}
        for site in ("shelwear", "apexf1")
    })
    shop.save_tilda_submission = save
    shop.read_tilda_submissions = read
    shop.save_tilda_email_message = orders.email
    shop.update_tilda_submission_im_number = orders.update
    shop.send_customer_email = Mock()
    shop.cdek_get_token = Mock(return_value="test-token")
    shop.find_cdek_order = Mock(return_value={"uuid": "test-uuid"})
    shop.update_cdek_order_number = Mock(return_value="request-uuid")
    shop._cdek_im_number_prefix = lambda account: "shop_"
    @asynccontextmanager
    async def lifespan(app):
        await outbox.initialize()
        try:
            yield
        finally:
            local_dir.cleanup()

    app = FastAPI(lifespan=lifespan)
    app.include_router(shop.router)
    app.mount("/static", StaticFiles(directory=shop.PROJECT_ROOT / "static"), name="static")

    @app.post("/__test__/reset")
    async def reset():
        nonlocal database_up
        database_up = True
        orders.rows.clear()
        orders.keys.clear()
        outbox.blocked_until.clear()
        with outbox._connect() as conn:
            conn.execute("DELETE FROM webhook_outbox")
        return {"ok": True}

    @app.post("/__test__/database/down")
    async def database_down():
        nonlocal database_up
        database_up = False
        return {"ok": True}

    @app.post("/__test__/database/recover")
    async def database_recover():
        nonlocal database_up
        database_up = True
        outbox.blocked_until.clear()
        with outbox._connect() as conn:
            conn.execute("UPDATE webhook_outbox SET retry_at=0")
        await outbox.run_once()
        return {"ok": True}

    @app.post("/__test__/legacy-taplink")
    async def legacy_taplink():
        # Reproduce an envelope saved before nested Taplink data was decoded.
        orders.rows["legacy-taplink"] = {
            "id": "legacy-taplink", "site": "shelwear",
            "created_at": datetime.now(timezone.utc).isoformat(), "payload_type": "form",
            "payload": {"event": "order", "data": json.dumps(taplink_order(), ensure_ascii=False)},
            "im_number": "", "email_messages": [],
        }
        return {"ok": True}

    return app
