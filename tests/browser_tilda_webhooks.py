"""Browser checks against tests.tilda_fixture (mocked SMTP/CDEK only)."""
import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def check(origin, chromium, artifacts=None):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=chromium, headless=True, args=["--no-sandbox"],
        )
        context = await browser.new_context(viewport={"width": 1440, "height": 1000})
        try:
            response = await context.request.post(origin + "/__test__/reset")
            assert response.ok, "Use tests.tilda_fixture, never a production server"
            page = await context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            raw = {
                "fullname": "Тестовый Покупатель", "email": "buyer@example.test", "phone": "79000000000",
                "order_id": "browser-one", "order_number": "27", "budget": "30", "shipping_price": "0",
                "payment_method_title": "Банковская карта",
                "offers": [{"title": 'Кроп "PURE BABY" белый', "price": "3000", "price_discount": "30",
                            "amount": "1", "budget": "30", "options": ["Размер: S"]}],
                "shipping_fields": json.dumps({"shipping_method": "Доставка Яндекс ПВЗ", "city": "Тестовый город", "addr1": "Тестовая улица, 1"}),
            }
            response = await context.request.post(origin + "/tilda/shelwear/webhook", data=raw)
            assert response.ok
            response = await context.request.post(origin + "/tilda/shelwear/webhook", data=raw)
            assert (await response.json())["duplicate"]
            await page.goto(origin + "/tilda/shelwear/form?secret=test-only")
            await page.locator(".submission-card").wait_for()
            assert await page.locator("[data-total-orders]").inner_text() == "1"
            assert await page.locator("[data-total-items]").inner_text() == "1"
            assert "30,00" in await page.locator("[data-total-sum]").inner_text()
            assert "30,00" in await page.locator("[data-products-sum]").inner_text()
            assert "0,00" in await page.locator("[data-delivery-sum]").inner_text()
            assert await page.locator("[data-cdek-orders]").inner_text() == "0 заказов"
            assert "Белые" in await page.locator("[data-colors-table]").inner_text()
            await page.locator("[data-size-filter]").select_option("S", force=True)
            await page.locator("[data-color-filter]").select_option("white", force=True)
            assert await page.locator(".submission-card").count() == 1
            await page.locator("[data-size-filter]").select_option("all", force=True)
            await page.locator("[data-color-filter]").select_option("all", force=True)
            await page.locator(".submission-header").click()
            card = page.locator(".submission-card")
            product = card.locator(".compact tbody tr")
            assert await product.locator("td").nth(3).inner_text() == "S"
            assert "30,00" in await product.locator("td").nth(5).inner_text()
            assert "Тестовый город" in await card.locator(".pretty-body").inner_text()
            assert await card.locator("[data-change-im-number]").count() == 0
            await card.locator("[data-email-modal]").click()
            assert "Заказ: 27" in await page.locator(".email-preview").inner_text()
            assert "30,00" in await page.locator(".email-preview").inner_text()
            await page.locator("[data-email-send]").click()
            await page.locator(".email-modal-backdrop").wait_for(state="detached")
            await page.locator(".submission-header").click()
            await page.locator(".email-history").wait_for()
            await page.locator("[data-search]").fill("PURE BABY")
            await page.wait_for_function("document.querySelectorAll('.submission-card').length === 1")
            await page.locator("[data-search]").fill("Нет такого товара")
            await page.wait_for_function("document.querySelectorAll('.submission-card').length === 0")
            await page.locator("[data-search]").fill("")
            await page.wait_for_function("document.querySelectorAll('.submission-card').length === 1")
            async with page.expect_download() as exported:
                await page.locator("[data-export-csv]").click()
            text = Path(await (await exported.value).path()).read_text(encoding="utf-8-sig")
            assert '"27"' in text and '"30"' in text and '"S"' in text
            if artifacts:
                await page.locator(".submission-header").click()
                await page.screenshot(path=str(artifacts / "shelwear-order.png"), full_page=True)

            cdek = {**raw, "order_id": "browser-cdek", "order_number": "28",
                    "shipping_fields": {"shipping_method": "Доставка СДЭК"}}
            response = await context.request.post(origin + "/tilda/shelwear/webhook", data=cdek)
            assert (await response.json())["auto_actions"]["new_im_number"] == "shop_28"
            await page.locator("[data-refresh]").click()
            await page.wait_for_function("document.querySelectorAll('.submission-card').length === 2")
            button = page.locator('[data-change-im-number][data-current-im-number="shop_28"]')
            await button.locator("xpath=ancestor::details").evaluate("element => element.open = true")
            page.once("dialog", lambda dialog: dialog.accept("shelwear_28"))
            # The request is followed by a success alert.
            page.on("dialog", lambda dialog: dialog.accept() if dialog.type == "alert" else None)
            await button.click()
            await page.locator('[data-change-im-number][data-current-im-number="shelwear_28"]').wait_for(state="attached")
            assert await page.locator("[data-cdek-orders]").inner_text() == "1 заказов"

            free = {**raw, "order_id": "browser-zero", "order_number": "29", "budget": 0,
                    "offers": [{**raw["offers"][0], "budget": 0, "price_discount": 0}]}
            await context.request.post(origin + "/tilda/shelwear/webhook", data=free)
            await page.locator("[data-refresh]").click()
            await page.wait_for_function("document.querySelectorAll('.submission-card').length === 3")
            assert "60,00" in await page.locator("[data-total-sum]").inner_text()
            assert "60,00" in await page.locator("[data-products-sum]").inner_text()
            tilda = {"Name": "Тестовый Покупатель", "Email": "buyer@example.test", "Phone": "79000000000",
                     "payment": json.dumps({"orderid": "100", "amount": "6088.77", "delivery_price": 688.77,
                         "delivery": "Доставка в ПВЗ СДЭК", "products": [{"name": "Зип-Худи", "price": "5400",
                             "quantity": 1, "options": [{"option": "Размер", "variant": "L"}]}]})}
            response = await context.request.post(origin + "/tilda/apexf1/webhook", form=tilda)
            assert response.ok
            await page.goto(origin + "/tilda/apexf1/form?secret=test-only")
            await page.locator(".submission-card").wait_for()
            assert "6\u00a0088,77" in await page.locator("[data-total-sum]").inner_text()
            assert "688,77" in await page.locator("[data-delivery-sum]").inner_text()
            assert not errors, errors
            print("Browser: cards, discounted totals, zero prices, email, CDEK rename, search and CSV passed")
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", default="http://127.0.0.1:8006")
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    if args.artifacts:
        args.artifacts.mkdir(parents=True, exist_ok=True)
    asyncio.run(check(args.origin, args.chromium, args.artifacts))
