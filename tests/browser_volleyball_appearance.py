"""Personal backgrounds/colors, persistence and net collision browser regressions."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.volleyball_physics import GROUND, Match


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
    await page.locator('#backgroundSelect').select_option(value)
    await page.wait_for_function('''id => JSON.parse(localStorage.getItem('volley.appearance.v1')).background === id
        && (id === 'paper' ? backgroundImage === null : backgroundImage?.complete)''', arg=value)


async def color(page, value):
    await page.locator('#customColorButton').click()
    await page.locator('#customColorDialog').wait_for(state='visible')
    await page.locator('#customColorHex').fill(value)
    assert await page.locator('#customColorPicker').input_value() == value.lower()
    await page.locator('#customColorForm button[type=submit]').click()
    await page.wait_for_function('() => !document.getElementById("customColorDialog").open')


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
                await page.wait_for_function('() => document.querySelectorAll("#backgroundSelect option").length===8')
            await host.evaluate(NET_CHECK,expected)
            print('PASS net: Python/JS agree; small-ball player pushes, local game, extrapolation and cap interpolation')
            await host.locator('#createRoomButton').click()
            await host.wait_for_function("() => /^[1-9][0-9]{3}$/.test(document.getElementById('roomCode').textContent)")
            code=await host.locator('#roomCode').inner_text()
            await guest.locator('#joinCode').fill(code);await guest.locator('#joinRoomButton').click()
            await host.wait_for_function("() => !document.getElementById('hostStartButton').disabled")
            frame_count=len(frames)
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
            await guest.reload()
            await guest.wait_for_function("() => onlineMode && playersArr[1].color==='#f59e0b' && backgroundImage?.complete")
            assert await guest.locator('#backgroundSelect').input_value()=='forest'
            await host.wait_for_function("() => !document.getElementById('hostStartButton').disabled")
            await host.locator('#hostStartButton').click()
            for page in (host,guest):
                await page.wait_for_function('() => isGameStarted && !isPaused')
                await page.wait_for_function('() => getComputedStyle(document.getElementById("hiddenUI")).opacity === "1"')
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
            assert await host.locator('#backgroundSelect').input_value()=='beach'
            assert await guest.locator('#backgroundSelect').input_value()=='forest'
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
