"""Local browser fixture; never mounts this module in the production app.

All storage, SMTP and CDEK calls use in-memory test doubles.
"""
import json
from datetime import datetime, timezone
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from tests.test_tilda_webhooks import MemoryOrders, shop, taplink_order


def create_app():
    orders = MemoryOrders()
    shop.config.TILDA_PROJECTS_JSON = json.dumps({
        site: {"secret": "test-only", "database_target": "test-db", "cdek_account": "test"}
        for site in ("shelwear", "apexf1")
    })
    shop.save_tilda_submission = orders.save
    shop.read_tilda_submissions = orders.read
    shop.save_tilda_email_message = orders.email
    shop.update_tilda_submission_im_number = orders.update
    shop.send_customer_email = Mock()
    shop.cdek_get_token = Mock(return_value="test-token")
    shop.find_cdek_order = Mock(return_value={"uuid": "test-uuid"})
    shop.update_cdek_order_number = Mock(return_value="request-uuid")
    shop._cdek_im_number_prefix = lambda account: "shop_"
    app = FastAPI()
    app.include_router(shop.router)
    app.mount("/static", StaticFiles(directory=shop.PROJECT_ROOT / "static"), name="static")

    @app.post("/__test__/reset")
    async def reset():
        orders.rows.clear()
        orders.keys.clear()
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
