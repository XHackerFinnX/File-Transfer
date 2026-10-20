"""Mobile layout and multi-touch checks against a running volleyball app."""

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright


async def visible_inside(page, selector):
    box = await page.locator(selector).bounding_box()
    assert box, selector
    width, height = await page.evaluate('[innerWidth, innerHeight]')
    assert box['x'] >= -1 and box['y'] >= -1, (selector, box)
    assert box['x']+box['width'] <= width+1, (selector, box, width)
    assert box['y']+box['height'] <= height+1, (selector, box, height)
    return box


async def swipe(page, cdp, start, end, cancel=False):
    def point(x,y):
        return dict(id=7,x=x,y=y,radiusX=5,radiusY=5)
    await cdp.send('Input.dispatchTouchEvent',dict(type='touchStart',touchPoints=[point(*start)]))
    for step in range(1,9):
        x=start[0]+(end[0]-start[0])*step/8
        y=start[1]+(end[1]-start[1])*step/8
        await cdp.send('Input.dispatchTouchEvent',dict(type='touchMove',touchPoints=[point(x,y)]))
        await page.wait_for_timeout(16)
    await page.wait_for_timeout(80)
    await cdp.send('Input.dispatchTouchEvent',dict(type='touchCancel' if cancel else 'touchEnd',touchPoints=[]))


async def settings_gestures(page, cdp):
    scroll=page.locator('.settings-scroll')
    await scroll.evaluate('el => el.scrollTop=0')
    slider=page.locator('#range-netHeight')
    await slider.scroll_into_view_if_needed()
    box=await slider.bounding_box()
    draft=await page.evaluate('JSON.stringify(settingsDraft)')
    before=await scroll.evaluate('el => el.scrollTop')
    start=(box['x']+box['width']*.65,box['y']+box['height']/2)
    await swipe(page,cdp,start,(start[0],start[1]-90))
    await page.wait_for_function('v => document.querySelector(".settings-scroll").scrollTop>v+20',arg=before)
    assert await page.evaluate('JSON.stringify(settingsDraft)')==draft
    # Start the return swipe directly over another visible slider.
    point=await page.evaluate('''() => {
        const area=document.querySelector('.settings-scroll').getBoundingClientRect();
        for(const input of document.querySelectorAll('.settings-scroll input[type=range]')){
            const box=input.getBoundingClientRect();
            if(box.top>=area.top+5&&box.bottom<=area.bottom-5)return [box.x+box.width*.5,box.y+box.height/2];
        }
        throw Error('No visible range for reverse scroll');
    }''')
    before=await scroll.evaluate('el => el.scrollTop')
    await swipe(page,cdp,point,(point[0],point[1]+80))
    await page.wait_for_function('v => document.querySelector(".settings-scroll").scrollTop<v-15',arg=before)
    assert await page.evaluate('JSON.stringify(settingsDraft)')==draft
    await slider.scroll_into_view_if_needed()
    box=await slider.bounding_box()
    x,y=box['x']+box['width']*.8,box['y']+box['height']/2
    await cdp.send('Input.dispatchTouchEvent',dict(type='touchStart',touchPoints=[dict(id=7,x=x,y=y)]))
    assert await page.evaluate('JSON.stringify(settingsDraft)')==draft
    await cdp.send('Input.dispatchTouchEvent',dict(type='touchEnd',touchPoints=[]))
    assert await page.evaluate('JSON.stringify(settingsDraft)')==draft
    await swipe(page,cdp,(box['x']+box['width']*.3,y),(x,y))
    assert await page.evaluate('JSON.stringify(settingsDraft)')!=draft
    value=await slider.input_value()
    assert await page.locator('#number-netHeight').input_value()==value
    # An interrupted horizontal gesture restores its original value.
    await swipe(page,cdp,(x,y),(box['x']+box['width']*.2,y),cancel=True)
    assert await slider.input_value()==value
    assert await page.locator('#number-netHeight').input_value()==value
    await slider.press('ArrowRight')
    assert float(await slider.input_value())==float(value)+.5
    await page.mouse.click(box['x']+box['width']*.2,y)
    assert float(await slider.input_value())<float(value)
    print('PASS settings: real touch scroll in both directions leaves sliders unchanged; horizontal drag, cancel, mouse and keyboard work')


async def check(origin, chromium, artifacts=None):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=chromium, headless=True, args=['--no-sandbox'])
        try:
            contexts = [await browser.new_context(viewport={'width': 393, 'height': 852}, is_mobile=True,
                                                   has_touch=True, device_scale_factor=3) for _ in range(2)]
            host, guest = [await context.new_page() for context in contexts]
            errors, sockets = [], []
            for page in (host, guest):
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('websocket', lambda ws: sockets.append(ws.url))
                await page.goto(origin+'/volleyball/online/')
                await page.wait_for_function("() => document.body.dataset.orientation === 'portrait'")
                await visible_inside(page, '#onlineHome .online-card')
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                await page.set_viewport_size({'width': 852, 'height': 393})
                await page.wait_for_function("() => document.body.dataset.orientation === 'landscape'")
                await visible_inside(page, '#onlineHome .online-card')
            await host.locator('#createRoomButton').click()
            await host.wait_for_function("() => /^[1-9][0-9]{3}$/.test(document.getElementById('roomCode').textContent)")
            await guest.locator('#joinCode').fill(await host.locator('#roomCode').inner_text())
            await guest.locator('#joinRoomButton').click()
            await host.wait_for_function("() => !document.getElementById('hostStartButton').disabled")
            await visible_inside(host, '#hostStartButton')
            if artifacts:
                await host.screenshot(path=str(artifacts/'mobile-room.png'))
            await host.locator('#hostStartButton').click()
            for page in (host, guest):
                await page.wait_for_function('() => isGameStarted')
                await visible_inside(page, '#tLeft')
                await visible_inside(page, '#tRight')
                await visible_inside(page, '#tJump')
                assert await page.locator('#tDown').count() == 0
                await page.wait_for_function("() => /[0-9]+ мс/.test(document.getElementById('hostPing').textContent) && /[0-9]+ мс/.test(document.getElementById('guestPing').textContent)")
                await visible_inside(page, '#hostPing')
                await visible_inside(page, '#guestPing')
            left = await host.locator('#tLeft').bounding_box()
            jump = await host.locator('#tJump').bounding_box()
            assert left['x'] < 852/2 < jump['x']
            old_arrow=min(74,max(56,393*.18))
            old_jump=min(88,max(68,393*.21))
            assert abs(left['width']-old_arrow*1.5)<1,left
            assert abs(left['height']-old_arrow*1.5)<1,left
            assert abs(jump['width']-old_jump*1.5)<1,jump
            cdp = await contexts[0].new_cdp_session(host)
            def finger(identity, box):
                return dict(id=identity, x=box['x']+box['width']/2, y=box['y']+box['height']/2, radiusX=5, radiusY=5)
            first, second = finger(1, left), finger(2, jump)
            before = await host.evaluate('playersArr[0].x')
            await cdp.send('Input.dispatchTouchEvent', dict(type='touchStart', touchPoints=[first]))
            await cdp.send('Input.dispatchTouchEvent', dict(type='touchStart', touchPoints=[first, second]))
            await host.wait_for_function('() => VolleyBridge.input().left && VolleyBridge.input().jump')
            await guest.wait_for_function('x => playersArr[0].x < x-5 && playersArr[0].y < 580', arg=before)
            # Releasing one finger must preserve the other direction.
            await cdp.send('Input.dispatchTouchEvent', dict(type='touchEnd', touchPoints=[second]))
            await host.wait_for_function('() => VolleyBridge.input().left && !VolleyBridge.input().jump',timeout=5000)
            await host.keyboard.down('KeyD')
            await host.keyboard.up('KeyD')
            assert await host.evaluate('VolleyBridge.input().left')
            # Pointer capture keeps a held direction until the finger is lifted,
            # even after it slides off the button.
            moved = first | dict(x=426, y=196)
            await cdp.send('Input.dispatchTouchEvent', dict(type='touchMove', touchPoints=[moved]))
            assert await host.evaluate('VolleyBridge.input().left')
            await cdp.send('Input.dispatchTouchEvent', dict(type='touchEnd', touchPoints=[]))
            await host.wait_for_function('() => Object.values(VolleyBridge.input()).every(v => !v)')
            await cdp.send('Input.dispatchTouchEvent', dict(type='touchStart', touchPoints=[first, second]))
            await cdp.send('Input.dispatchTouchEvent', dict(type='touchCancel', touchPoints=[]))
            await host.wait_for_function('() => Object.values(VolleyBridge.input()).every(v => !v)')
            # Rotate with a finger down. Input is cleared and the same room and
            # WebSocket survive the viewport/orientation changes.
            await cdp.send('Input.dispatchTouchEvent', dict(type='touchStart', touchPoints=[first]))
            for width, height in [(393, 852), (568, 320), (1024, 600), (320, 568), (852, 393)]:
                await host.set_viewport_size(dict(width=width, height=height))
                orientation = 'landscape' if width > height else 'portrait'
                await host.wait_for_function('o => document.body.dataset.orientation === o', arg=orientation)
                await host.wait_for_function('''() => onlineArenaView.width === hostArenaView.width
                    && onlineArenaView.height === hostArenaView.height''')
                for selector in ('#tLeft', '#tRight', '#tJump', '#menuBtn', '.scoreboard'):
                    await visible_inside(host, selector)
                assert await host.evaluate('canvas.width <= innerWidth*2+1 && canvas.height <= innerHeight*2+1')
                assert await host.evaluate('Object.values(VolleyBridge.input()).every(v => !v)')
                assert await host.evaluate('document.documentElement.scrollWidth <= innerWidth')
                right=await host.locator('#tRight').bounding_box()
                jump_box=await host.locator('#tJump').bounding_box()
                assert right['x']+right['width']<jump_box['x'],(right,jump_box)
                if width > height:
                    arena = await host.evaluate('''() => {
                        const dpr=canvas.width/innerWidth;
                        return {left:VIEW_OFFSET_X/dpr,right:(VIEW_OFFSET_X+LOGICAL_WIDTH*GAME_SCALE_X)/dpr};
                    }''')
                    assert arena['left'] >= 0 and arena['right'] <= width, arena
                    assert arena['right']-arena['left'] >= width*.9, arena
            await cdp.send('Input.dispatchTouchEvent', dict(type='touchCancel', touchPoints=[]))
            if artifacts:
                await host.screenshot(path=str(artifacts/'mobile-match.png'))
            await host.locator('#menuBtn').click()
            await host.locator('#hostSettingsButton').click()
            await host.locator('#physicsModal').wait_for(state='visible')
            for width, height in [(568, 320), (393, 852), (852, 393)]:
                await host.set_viewport_size(dict(width=width, height=height))
                await host.wait_for_function('h => canvas.style.height === h+"px"', arg=height)
                await visible_inside(host, '.settings-window')
                await visible_inside(host, '#physicsModal .close-settings')
                assert await host.locator('.settings-scroll').evaluate('el => el.scrollWidth <= el.clientWidth+1')
                # The settings footer stays reachable while only its contents scroll.
                for button in await host.locator('.settings-confirm button').all():
                    await button.scroll_into_view_if_needed()
                    box = await button.bounding_box()
                    assert box['y']+box['height'] <= height+1, box
            await settings_gestures(host,cdp)
            if artifacts:
                await host.screenshot(path=str(artifacts/'mobile-settings.png'))
            assert len(sockets) == 2, sockets
            assert not errors, errors
            print('PASS mobile: two players, simultaneous movement/jump, release/cancel/capture and mixed keyboard input')
            print('PASS mobile: portrait/landscape rotation, 320–1024px layouts, touch controls and settings; no JS errors')
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
