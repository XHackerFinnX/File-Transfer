"""Manual disaster restore UI on desktop/mobile, against the isolated fixture only."""

import argparse
import asyncio
import json

from playwright.async_api import async_playwright


async def check(origin, chromium):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=chromium, headless=True, args=["--no-sandbox"])
        try:
            for width, height in ((1440, 1000), (390, 844)):
                context = await browser.new_context(viewport={"width": width, "height": height})
                assert (await context.request.post(origin + "/__test__/reset")).ok
                await context.request.post(origin + "/__test__/google/enable")
                await context.request.post(origin + "/__test__/database/down")
                payload = {"Name": "Тестовый клиент", "Email": "buyer@example.test", "payment": {
                    "orderid": "backup-order", "amount": "3500", "delivery": "Самовывоз",
                    "products": [{"name": "Футболка", "sku": "shirt-L", "quantity": 1, "price": "3500"}]}}
                response = await context.request.post(origin + "/tilda/shelwear/webhook", data=payload)
                assert response.ok and (await response.json())["external_backup"]["status"] == "saved"
                effects = await (await context.request.get(origin + "/__test__/effects")).json()
                copy, = effects["copies"]
                assert effects["smtp_calls"] == 0
                page = await context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto(origin + "/tilda/shelwear/form?secret=test-only")
                await page.get_by_text("Копия в Google Таблице", exact=True).wait_for()
                assert await page.get_by_role("link", name="Резервная таблица").get_attribute("href") == "https://docs.google.com/spreadsheets/d/test_spreadsheet_id/edit"
                # Simulate losing the local/PG test storage, while retaining the external copy.
                await context.request.post(origin + "/__test__/reset")
                await page.reload()
                await page.get_by_role("button", name="Добавить запись", exact=True).click()
                await page.locator("[data-record-import]").fill(json.dumps(copy, ensure_ascii=False))
                await page.locator("[data-record-fill]").click()
                assert await page.locator('[data-record-field="id"]').input_value() == copy["submission"]["id"]
                assert json.loads(await page.locator('[data-record-field="payload"]').input_value()) == payload
                assert await page.locator('[data-record-field="site"]').input_value() == "shelwear"
                await page.locator("[data-record-save]").click()
                await page.locator("[data-record-form]").wait_for(state="detached")
                await page.locator(".submission-card").wait_for()
                effects = await (await context.request.get(origin + "/__test__/effects")).json()
                assert effects["smtp_calls"] == 0 and effects["cdek_updates"] == 0
                await page.get_by_role("button", name="Добавить запись", exact=True).click()
                await page.locator("[data-record-import]").fill("broken JSON")
                await page.locator("[data-record-fill]").click()
                assert "Не удалось заполнить" in await page.locator("[data-record-status]").inner_text()
                await page.keyboard.press("Escape")
                await page.locator("[data-record-form]").wait_for(state="detached")
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert not errors, errors
                await context.close()
            print("Browser restore passed: Google backup badge/link, full JSON import, all fields, mobile scrolling, no duplicate SMTP/CDEK")
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", default="http://127.0.0.1:8010")
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    arguments = parser.parse_args()
    asyncio.run(check(arguments.origin, arguments.chromium))
