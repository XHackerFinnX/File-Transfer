"""Optional browser smoke check; requires Python Playwright and Chromium."""

import argparse
import asyncio
from urllib.parse import urlsplit

from playwright.async_api import async_playwright


async def check(origin, chromium):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=chromium, headless=True, args=["--no-sandbox"])
        try:
            contexts = [await browser.new_context(viewport={"width": 1440, "height": 1000}) for _ in range(2)]
            host, guest = [await context.new_page() for context in contexts]
            errors, sockets = [], []
            for page in (host, guest):
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("websocket", lambda ws: sockets.append(ws.url))
                await page.goto(origin+"/volleyball/online/")
                await page.wait_for_function("() => !document.getElementById('createRoomButton').disabled")
            await host.locator("#createRoomButton").click()
            await host.wait_for_function("() => !document.getElementById('onlineRoom').hidden && /^[1-9][0-9]{3}$/.test(document.getElementById('roomCode').textContent)")
            code = await host.locator("#roomCode").inner_text()
            await guest.locator("#joinCode").fill(code)
            await guest.locator("#joinRoomButton").click()
            await host.wait_for_function("() => document.getElementById('onlineStatus2').textContent === 'Подключён'")
            await guest.wait_for_function("() => document.getElementById('roomRole').textContent === 'ГОСТЬ'")
            await host.locator("#hostStartButton").click()
            for page in (host, guest):
                await page.wait_for_function("() => document.getElementById('onlineRoom').hidden && isGameStarted")
            before = await host.evaluate("playersArr[0].x")
            await host.keyboard.down("KeyD")
            await host.keyboard.down("KeyW")
            await host.wait_for_timeout(170)
            await host.keyboard.up("KeyD")
            await host.keyboard.up("KeyW")
            await guest.wait_for_function("x => playersArr[0].x > x+10", arg=before)
            for page in (host, guest):
                await page.wait_for_function("() => score1+score2 > 0", timeout=10000)
            scores = [await page.evaluate("[score1,score2]") for page in (host, guest)]
            assert scores[0] == scores[1], scores
            url = urlsplit(origin)
            expected = ("wss" if url.scheme == "https" else "ws")+"://"+url.netloc+"/volleyball/ws"
            assert sockets == [expected, expected], sockets
            assert not errors, errors
            print("PASS: two browsers create/join/start; remote movement and synchronized score", scores[0])
            print("PASS: same-origin WebSocket; no JavaScript errors")
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", nargs="?", default="http://127.0.0.1:8000")
    parser.add_argument("--chromium", default=None)
    args = parser.parse_args()
    asyncio.run(check(args.origin.rstrip("/"), args.chromium))
