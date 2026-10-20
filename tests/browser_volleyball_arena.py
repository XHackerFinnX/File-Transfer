"""Host-defined arena geometry across mixed screens, rotation and seat recovery."""

import argparse
import asyncio
import json
import math
from pathlib import Path

from playwright.async_api import async_playwright


async def sync_view(host, guest):
    await host.wait_for_function('''() => onlineArenaView.width === hostArenaView.width
        && onlineArenaView.height === hostArenaView.height''')
    view = await host.evaluate('onlineArenaView')
    await guest.wait_for_function('''v => onlineArenaView?.width === v.width
        && onlineArenaView?.height === v.height''', arg=view)
    return view


async def geometry(page):
    return await page.evaluate('''() => {
        const dpr=Math.min(2,devicePixelRatio||1);
        return {view:onlineArenaView, world:[LOGICAL_WIDTH,LOGICAL_HEIGHT],
            scaleX:GAME_SCALE_X/dpr, scaleY:GAME_SCALE_Y/dpr,
            left:(VIEW_OFFSET_X-24*GAME_SCALE_X)/dpr, top:VIEW_OFFSET_Y/dpr,
            width:(LOGICAL_WIDTH+48)*GAME_SCALE_X/dpr, height:LOGICAL_HEIGHT*GAME_SCALE_Y/dpr,
            screen:[innerWidth,innerHeight], round:onlineRound};
    }''')


async def same_arena(host, guest):
    await sync_view(host, guest)
    a, b = await geometry(host), await geometry(guest)
    assert a['view'] == b['view'] and a['world'] == b['world'], (a, b)
    # Both axes must use the same factor when fitting the host's projection.
    assert math.isclose(a['scaleX']/b['scaleX'], a['scaleY']/b['scaleY'], rel_tol=1e-9), (a, b)
    for arena in (a, b):
        assert math.isclose(arena['width']/arena['height'], arena['view']['width']/arena['view']['height']), arena
        assert arena['left'] >= -1 and arena['top'] >= -1, arena
        assert arena['left']+arena['width'] <= arena['screen'][0]+1, arena
        assert arena['top']+arena['height'] <= arena['screen'][1]+1, arena
    # A fixed world displacement occupies the same fraction of the field.
    for scale, span in [('scaleX', 'width'), ('scaleY', 'height')]:
        assert math.isclose(100*a[scale]/a[span], 100*b[scale]/b[span]), (a, b)
    return a, b


async def check(origin, chromium, artifacts=None):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=chromium, headless=True, args=['--no-sandbox'])
        try:
            host_context = await browser.new_context(viewport={'width': 1024, 'height': 420},
                                                     is_mobile=True, has_touch=True, device_scale_factor=3)
            guest_context = await browser.new_context(viewport={'width': 568, 'height': 320},
                                                      is_mobile=True, has_touch=True, device_scale_factor=1)
            host, guest = await host_context.new_page(), await guest_context.new_page()
            errors, guest_views = [], []
            for page in (host, guest):
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.goto(origin+'/volleyball/online/')
            guest.on('websocket', lambda ws: ws.on('framesent', lambda raw:
                guest_views.append(raw) if json.loads(raw).get('type') == 'arena_view' else None))
            await host.locator('#createRoomButton').click()
            await host.wait_for_function("() => /^[1-9][0-9]{3}$/.test(document.getElementById('roomCode').textContent)")
            code = await host.locator('#roomCode').inner_text()
            await guest.locator('#joinCode').fill(code)
            await guest.locator('#joinRoomButton').click()
            await host.wait_for_function("() => !document.getElementById('hostStartButton').disabled")
            await same_arena(host, guest)
            await host.locator('#hostStartButton').click()
            for page in (host, guest):
                await page.wait_for_function('() => isGameStarted && !isPaused')
            reference = await host.evaluate('onlineArenaView')
            for width, height in [(393, 852), (1365, 320), (852, 393)]:
                await guest.set_viewport_size(dict(width=width, height=height))
                await guest.wait_for_function('h => canvas.style.height === h+"px"', arg=height)
                a, b = await same_arena(host, guest)
                assert a['view'] == reference, (a, reference)
                if width == 1365:
                    assert b['left'] > 200, b  # White side bars, no extra stretch.
            if artifacts:
                await guest.screenshot(path=str(artifacts/'arena-small-guest.png'))
            # Different notches/paddings must not give the guest a new projection.
            for page, left, right in [(host, 52, 24), (guest, 24, 12)]:
                await page.evaluate('''v => {
                    document.documentElement.style.setProperty('--safe-left',v[0]+'px');
                    document.documentElement.style.setProperty('--safe-right',v[1]+'px');
                    window.dispatchEvent(new Event('resize'));
                }''', [left, right])
            await host.wait_for_function('() => hostArenaView.width === 948')
            await same_arena(host, guest)
            for width, height in [(393, 852), (568, 320), (1024, 420)]:
                round_before = await host.evaluate('onlineRound')
                await host.set_viewport_size(dict(width=width, height=height))
                await host.wait_for_function('h => canvas.style.height === h+"px"', arg=height)
                await same_arena(host, guest)
                assert await host.evaluate('onlineRound') == round_before
                assert await host.evaluate('isGameStarted && !isPaused')
                assert await guest.evaluate('isGameStarted && !isPaused')
            assert not guest_views, guest_views
            print('PASS shared arena: mixed mobile sizes/DPR, independent guest rotation, host rotation, safe areas and letterboxing')
            # Recover the same guest seat on a desktop screen, keeping room view.
            saved = await guest.evaluate("sessionStorage.getItem('volley.online.session.v1')")
            await guest_context.close()
            await host.wait_for_function('() => isPaused')
            desktop_context = await browser.new_context(viewport={'width': 1280, 'height': 800})
            desktop = await desktop_context.new_page()
            desktop.on('pageerror', lambda error: errors.append(str(error)))
            await desktop.add_init_script("sessionStorage.setItem('volley.online.session.v1',"+json.dumps(saved)+")")
            await desktop.goto(origin+'/volleyball/online/')
            await desktop.wait_for_function('() => onlineMode && onlineArenaView !== null')
            await same_arena(host, desktop)
            assert await desktop.locator('#roomCode').inner_text() == code
            await host.wait_for_function("() => !document.getElementById('hostStartButton').disabled")
            await host.locator('#hostStartButton').click()
            for page in (host, desktop):
                await page.wait_for_function('() => isGameStarted && !isPaused')
            await same_arena(host, desktop)
            if artifacts:
                await host.screenshot(path=str(artifacts/'arena-wide-host.png'))
                await desktop.screenshot(path=str(artifacts/'arena-desktop-guest.png'))
            assert not errors, errors
            print('PASS shared arena: desktop guest restores the same view and resumes; no JS errors')
        finally:
            await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('origin', nargs='?', default='http://127.0.0.1:8000')
    parser.add_argument('--chromium')
    parser.add_argument('--artifacts', type=Path)
    args = parser.parse_args()
    if args.artifacts:
        args.artifacts.mkdir(parents=True, exist_ok=True)
    asyncio.run(check(args.origin.rstrip('/'), args.chromium, args.artifacts))
