"""Personal backgrounds/colors, persistence and net collision browser regressions."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.volleyball_physics import GROUND, Match
from tests.browser_volleyball_mobile import swipe


NET_CHECK = r"""async expected => {
  const {Match} = await import('/volleyball/online/shared/physics.js');
  const {SnapshotBuffer} = await import('/volleyball/online/shared/snapshots.js');
  for(const side of [-1,1]){
    const m=new Match({ballSize:50,netWidth:4,maxTouches:0});m.start();m.serving=false;
    Object.assign(m.players[side===-1?0:1],{x:550+side*45,y:585});
    Object.assign(m.ball,{x:550+side*27,y:500,vx:-side*4,vy:0});m.step();
    if((m.ball.x-550)*side<24||m.ball.vx*side<=0)throw Error('JS player pushes small ball through net');
    for(const k of ['x','y','vx','vy'])if(Math.abs(m.ball[k]-expected[side===-1?0:1][k])>1e-8)
      throw Error('Python/JS net mismatch: '+k);
  }
  const m=new Match({netWidth:4,ballSpeed:200});m.start();m.serving=false;
  const state=m.snapshot();Object.assign(state.ball,{x:490,y:350,vx:48,vy:0});
  const buffer=new SnapshotBuffer();buffer.push(state,1,0,1000);
  const extrapolated=buffer.sample(90,m.settings);
  if(extrapolated.ball.x>506)throw Error('Extrapolation crosses net');
  const above=structuredClone(state);Object.assign(above.ball,{y:100});
  buffer.reset();buffer.push(above,1,0,1000);
  if(buffer.sample(90,m.settings).ball.x<=550)throw Error('Legal flight above net is blocked');
  const first=structuredClone(state),second=structuredClone(state);
  Object.assign(first.ball,{x:490,y:m.net.y-32});Object.assign(second.ball,{x:610,y:m.net.y-32});
  buffer.reset();buffer.push(first,1,0,1000);buffer.push(second,2,33,1033);
  const interpolated=buffer.sample(60,m.settings).ball;
  if(Math.hypot(interpolated.x-550,interpolated.y-m.net.y)<44)throw Error('Interpolation clips net cap');
  // Exercise the actual local game's legacy loop as well as the shared model.
  appSettings=normalizeSettings({ballSize:50,netWidth:4,maxTouches:0});configurePlayers();resize();
  isBotEnabled=false;isGameOver=false;isServing=false;
  Object.assign(player1,{x:505,y:585,vx:0,vy:0,scaleX:1,scaleY:1,sVelX:0,sVelY:0});
  Object.assign(player2,{x:825,y:585,vx:0,vy:0,scaleX:1,scaleY:1,sVelX:0,sVelY:0});
  Object.assign(ball,{x:523,y:500,vx:4,vy:0});update();
  if(ball.x>526||ball.vx>=0)throw Error('Local game pushes ball through net');
  VolleyBridge.local();
}"""


async def background(page, value):
    if await page.locator('#backgroundSelect').get_attribute('aria-expanded')!='true':
        await page.locator('#backgroundSelect').click()
    await page.locator('#backgroundMenu .appearance-choice-name[data-choice="'+value+'"]').click()
    await page.wait_for_function('''id => JSON.parse(localStorage.getItem('volley.appearance.v1')).background === id
        && (id === 'paper' ? backgroundImage === null : backgroundImage?.complete)''', arg=value)


async def color(page, value):
    await page.locator('#customColorButton').click()
    await page.locator('#customColorDialog').wait_for(state='visible')
    await page.locator('#customColorHex').fill(value)
    assert await page.locator('#customColorPicker').evaluate('el => el.value') == value.lower()
    await page.locator('#customColorForm button[type=submit]').click()
    await page.wait_for_function('() => !document.getElementById("customColorDialog").open')


async def choose_ball(page, value):
    if await page.locator('#ballSelect').get_attribute('aria-expanded')!='true':
        await page.locator('#ballSelect').click()
    await page.locator('#ballMenu .appearance-choice-name[data-choice="'+value+'"]').click()
    await page.wait_for_function('id => ballDesign===id',arg=value)


async def check_visual_menus(host,guest,artifacts):
    await host.locator('#backgroundSelect').click()
    thumbnail=host.locator('#backgroundMenu [data-id="beach"] .appearance-thumbnail-button')
    await thumbnail.scroll_into_view_if_needed()
    await host.wait_for_function('''() => {
        const image=document.querySelector('#backgroundMenu [data-id="beach"] img');
        return image.complete&&image.naturalWidth===240;
    }''')
    if artifacts:
        await host.screenshot(path=str(artifacts/'background-thumbnail-menu.png'))
    await thumbnail.click()
    await host.wait_for_function('''() => {
        const image=document.querySelector('#appearancePreviewContent img');
        return document.getElementById('appearancePreviewDialog').open&&image?.complete&&image.naturalWidth>1000;
    }''')
    assert await host.locator('#backgroundSelect').evaluate('el => el.value')=='paper'
    assert await host.evaluate('backgroundImage===null')
    assert await guest.evaluate('backgroundImage===null')
    if artifacts:
        await host.screenshot(path=str(artifacts/'background-preview.png'))
    await host.locator('#closeAppearancePreview').click()
    assert await host.locator('#backgroundMenu').is_visible()
    assert await thumbnail.evaluate('el => document.activeElement===el')
    await thumbnail.click();await host.keyboard.press('Escape')
    assert not await host.locator('#appearancePreviewDialog').is_visible()
    assert await host.locator('#backgroundSelect').evaluate('el => el.value')=='paper'
    await background(host,'beach')
    assert await host.locator('#backgroundMenu').is_hidden()
    assert await host.locator('#backgroundSelectedName').inner_text()=='Пляж с пальмами'
    assert await guest.evaluate('backgroundImage===null')
    await background(host,'paper')
    await host.locator('#ballSelect').click()
    assert await host.locator('#ballMenu .appearance-choice').count()==6
    await host.locator('#ballMenu [data-id="neon"] .appearance-thumbnail-button').click()
    assert await host.locator('#appearancePreviewContent canvas').is_visible()
    assert await host.evaluate('ballDesign')=='classic'
    await host.locator('#closeAppearancePreview').click()
    state=await host.evaluate('JSON.stringify({ball,settings:appSettings})')
    sprites=set()
    for design in ('classic','match','beach','basketball','football','neon'):
        await choose_ball(host,design)
        sprites.add(await host.evaluate('ballCanvas.toDataURL()'))
        assert await host.evaluate('JSON.stringify({ball,settings:appSettings})')==state
        assert await guest.evaluate('ballDesign')=='classic'
    assert len(sprites)==6
    await choose_ball(guest,'basketball')
    assert await host.evaluate('ballDesign')=='neon'
    sprite=await host.evaluate('ballCanvas.toDataURL()')
    await host.evaluate('resize()')
    assert await host.evaluate('ballCanvas.toDataURL()')==sprite
    if artifacts:
        await host.locator('#ballSelect').click()
        await host.screenshot(path=str(artifacts/'ball-design-menu.png'))
        await host.keyboard.press('Escape')
    print('PASS visual menus: thumbnail opens/escapes preview without selecting; name selects; six distinct ball designs preserve physics and stay private')


async def check_palette(host,guest,contexts,artifacts):
    await host.locator('#customColorButton').click()
    await host.locator('#customColorPicker').click()
    await host.locator('#colorPalette').wait_for(state='visible')
    await host.locator('#customColorHue').press('Home')
    assert await host.locator('#customColorHue').input_value()=='0'
    box=await host.locator('#colorPalette').bounding_box()
    assert box['width']>200 and box['height']>100,box
    cdp=await contexts[0].new_cdp_session(host)
    await swipe(host,cdp,(box['x']+box['width']*.2,box['y']+box['height']*.2),
                (box['x']+box['width']*.8,box['y']+box['height']*.8))
    chosen=await host.locator('#customColorHex').input_value()
    assert chosen!='#12abcd',chosen
    assert await host.locator('#customColorPicker').evaluate('el => el.value')==chosen
    assert await host.evaluate('playersArr[0].color')=='#12abcd'
    assert await guest.evaluate('playersArr[0].color')=='#12abcd'
    await host.locator('#customColorHex').fill('#00ff00')
    assert await host.locator('#customColorHue').input_value()=='120'
    assert await host.locator('#colorPaletteMarker').evaluate('el => [el.style.left,el.style.top]')==['100%','0%']
    await host.locator('#colorPalette').press('ArrowLeft')
    assert await host.locator('#customColorHex').input_value()!='#00ff00'
    if artifacts:
        await host.screenshot(path=str(artifacts/'mobile-color-palette.png'))
    await host.locator('#cancelCustomColor').click()
    await guest.locator('#customColorButton').click()
    await guest.locator('#customColorPicker').click()
    box=await guest.locator('#colorPalette').bounding_box()
    await guest.mouse.move(box['x']+box['width']*.2,box['y']+box['height']*.2)
    await guest.mouse.down()
    await guest.mouse.move(box['x']+box['width']*.7,box['y']+box['height']*.7,steps=5)
    await guest.mouse.up()
    assert await guest.locator('#customColorHex').input_value()!='#f59e0b'
    await guest.locator('#cancelCustomColor').click()
    print('PASS palette: big in-page color field, real touch/mouse dragging, hue, keyboard and HEX synchronization; preview is private until save')


async def check(origin, chromium, artifacts=None):
    expected=[]
    for side in (-1,1):
        m=Match({'ballSize':50,'netWidth':4,'maxTouches':0});m.start();m.serving=False
        m.players[0 if side==-1 else 1].update(x=550+side*45,y=GROUND)
        m.ball.update(x=550+side*27,y=500,vx=-side*4,vy=0);m.step()
        expected.append({k:m.ball[k] for k in ('x','y','vx','vy')})
    async with async_playwright() as playwright:
        browser=await playwright.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            contexts=[await browser.new_context(viewport={'width':852,'height':393},is_mobile=True,has_touch=True),
                      await browser.new_context(viewport={'width':1280,'height':800})]
            host,guest=[await context.new_page() for context in contexts]
            errors,frames=[],[]
            for page in (host,guest):
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('websocket',lambda ws:ws.on('framesent',lambda raw:frames.append(json.loads(raw))))
                await page.goto(origin+'/volleyball/online/')
                await page.wait_for_function('() => document.querySelectorAll("#backgroundMenu .appearance-choice").length===8')
            await host.evaluate(NET_CHECK,expected)
            print('PASS net: Python/JS agree; small-ball player pushes, local game, extrapolation and cap interpolation')
            await host.locator('#createRoomButton').click()
            await host.wait_for_function("() => /^[1-9][0-9]{3}$/.test(document.getElementById('roomCode').textContent)")
            code=await host.locator('#roomCode').inner_text()
            await guest.locator('#joinCode').fill(code);await guest.locator('#joinRoomButton').click()
            await host.wait_for_function("() => !document.getElementById('hostStartButton').disabled")
            frame_count=len(frames)
            await check_visual_menus(host,guest,artifacts)
            # Decode every supplied image; restore the original and choose two
            # different backgrounds without sending match settings to the peer.
            for value in ('blue','pencil','doodles','sea','beach','lined','forest','paper','beach'):
                await background(host,value)
            await background(guest,'forest')
            assert all(p['type'] in ('ping','pong_ack') or p['type']=='input' and p['mask']==0
                       for p in frames[frame_count:]),frames[frame_count:]
            assert await host.evaluate("JSON.parse(localStorage.getItem('volley.appearance.v1')).background")=='beach'
            assert await guest.evaluate("JSON.parse(localStorage.getItem('volley.appearance.v1')).background")=='forest'
            await color(host,'#12aBcD');await color(guest,'#f59e0b')
            for page in (host,guest):
                await page.wait_for_function("() => playersArr[0].color==='#12abcd' && playersArr[1].color==='#f59e0b'")
            await check_palette(host,guest,contexts,artifacts)
            await host.locator('#customColorButton').click()
            await host.locator('#customColorHex').fill('invalid')
            await host.locator('#customColorForm button[type=submit]').click()
            assert await host.locator('#customColorDialog').is_visible()
            await host.locator('#customColorHex').fill('#aabbcc')
            await host.locator('#cancelCustomColor').click()
            assert await host.evaluate('playersArr[0].color')=='#12abcd'
            # Presets remain usable; reopening the palette retains the saved custom color.
            await host.locator('#ownColors button').nth(1).click()
            await guest.wait_for_function("() => playersArr[0].color==='#ff0000'")
            await host.locator('#customColorButton').click()
            assert await host.locator('#customColorHex').input_value()=='#12abcd'
            await host.locator('#customColorForm button[type=submit]').click()
            await guest.wait_for_function("() => playersArr[0].color==='#12abcd'")
            await guest.locator('#toggleStatsButton').click()
            assert await guest.evaluate('document.body.dataset.stats')=='hidden'
            assert await host.evaluate('document.body.dataset.stats')=='visible'
            await guest.reload()
            await guest.wait_for_function("() => onlineMode && playersArr[1].color==='#f59e0b' && backgroundImage?.complete")
            assert await guest.locator('#backgroundSelect').evaluate('el => el.value')=='forest'
            assert await guest.evaluate('ballDesign')=='basketball'
            assert await host.evaluate('ballDesign')=='neon'
            assert await guest.evaluate('document.body.dataset.stats')=='hidden'
            assert await guest.locator('#toggleStatsButton').inner_text()=='ПОКАЗАТЬ ПИНГ И FPS'
            await host.wait_for_function("() => !document.getElementById('hostStartButton').disabled")
            await host.locator('#hostStartButton').click()
            for page in (host,guest):
                await page.wait_for_function('() => isGameStarted && !isPaused')
                await page.wait_for_function('() => getComputedStyle(document.getElementById("hiddenUI")).opacity === "1"')
            for selector in ('#hostPing','#guestPing','#networkHealth'):
                assert await guest.locator(selector).is_hidden(),selector
                assert await host.locator(selector).is_visible(),selector
            if artifacts:
                await host.screenshot(path=str(artifacts/'background-mobile-host.png'))
                await guest.screenshot(path=str(artifacts/'background-desktop-guest.png'))
            # Persisted choices also apply when creating/joining a new room.
            await host.locator('#menuBtn').click();await host.locator('#leaveRoomButton').click()
            await host.locator('#createRoomButton').click()
            await host.wait_for_function("() => onlineMode && /^[1-9][0-9]{3}$/.test(document.getElementById('roomCode').textContent)")
            await guest.wait_for_function('() => !onlineMode')
            await guest.locator('#joinCode').fill(await host.locator('#roomCode').inner_text())
            await guest.locator('#joinRoomButton').click()
            for page in (host,guest):
                await page.wait_for_function("() => onlineMode && playersArr[0].color==='#12abcd' && playersArr[1].color==='#f59e0b'")
            assert await host.locator('#backgroundSelect').evaluate('el => el.value')=='beach'
            assert await guest.locator('#backgroundSelect').evaluate('el => el.value')=='forest'
            assert await guest.locator('#ballSelect').evaluate('el => el.value')=='basketball'
            assert await host.locator('#ballSelect').evaluate('el => el.value')=='neon'
            assert await guest.evaluate('document.body.dataset.stats')=='hidden'
            await guest.locator('#toggleStatsButton').click()
            assert await guest.evaluate('document.body.dataset.stats')=='visible'
            print('PASS stats: ping/FPS hidden for only one player, saved across reload/rooms and shown again from the menu')
            assert not errors,errors
            print('PASS appearance: all 7 images, independent backgrounds, palette/HEX/save/cancel/presets, reload and new room')
        finally:
            await browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('origin',nargs='?',default='http://127.0.0.1:8000')
    parser.add_argument('--chromium')
    parser.add_argument('--artifacts',type=Path)
    args=parser.parse_args()
    if args.artifacts:args.artifacts.mkdir(parents=True,exist_ok=True)
    asyncio.run(check(args.origin.rstrip('/'),args.chromium,args.artifacts))
