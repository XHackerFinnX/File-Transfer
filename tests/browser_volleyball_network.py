"""Two browser connections with unequal latency, visible RTT and outage recovery."""

import argparse
import asyncio
from playwright.async_api import async_playwright

# Keep WebSocket frame order, as TCP does. Faults affect application traffic
# without disconnecting the browser socket, exercising the stale-link watchdog.
FAULTS = r"""delay => {
  window.networkFault={delay,blackhole:false};
  const Native=window.WebSocket;
  window.WebSocket=class extends Native {
    constructor(...args){super(...args);this.delivery=0;}
    addEventListener(type,listener,options){
      if(type!=='message')return super.addEventListener(type,listener,options);
      return super.addEventListener(type,event=>{
        if(networkFault.blackhole)return;
        this.delivery=Math.max(performance.now()+networkFault.delay,this.delivery);
        setTimeout(()=>{if(!networkFault.blackhole)listener.call(this,event);},Math.max(0,this.delivery-performance.now()));
      },options);
    }
    send(data){
      if(networkFault.blackhole)return;
      setTimeout(()=>{if(this.readyState===Native.OPEN&&!networkFault.blackhole)super.send(data);},networkFault.delay);
    }
  };
}"""


async def check(origin, chromium):
    async with async_playwright() as playwright:
        browser=await playwright.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            contexts=[await browser.new_context() for _ in range(2)]
            host,guest=[await context.new_page() for context in contexts]
            errors,sockets=[],[]
            for page,delay in zip((host,guest),(40,110)):
                await page.add_init_script('('+FAULTS+')('+str(delay)+')')
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('websocket',lambda ws:sockets.append(ws.url))
                await page.goto(origin+'/volleyball/online/')
            await host.locator('#createRoomButton').click()
            await host.wait_for_function("() => /^[1-9][0-9]{3}$/.test(document.getElementById('roomCode').textContent)")
            code=await host.locator('#roomCode').inner_text()
            await guest.locator('#joinCode').fill(code)
            await guest.locator('#joinRoomButton').click()
            await host.wait_for_function("() => !document.getElementById('hostStartButton').disabled")
            await host.locator('#hostStartButton').click()
            for page in (host,guest):
                await page.wait_for_function("() => isGameStarted && /[0-9]+ мс/.test(document.getElementById('hostPing').textContent) && /[0-9]+ мс/.test(document.getElementById('guestPing').textContent)")
            pings=await host.evaluate("[...['hostPing','guestPing']].map(id=>Number(document.getElementById(id).textContent.match(/[0-9]+/)[0]))")
            assert pings[0]>=60 and pings[1]>=180 and pings[1]>pings[0]+50,pings
            print('PASS distinct visible RTT (simulated network): host',pings[0],'ms, guest',pings[1],'ms')
            await host.wait_for_timeout(1100)
            assert 'FPS' in await host.locator('#networkHealth').inner_text()
            # Lose all game messages while leaving TCP apparently connected.
            await host.evaluate('networkFault.blackhole=true')
            await host.wait_for_function("() => !document.getElementById('onlineRoom').hidden",timeout=7000)
            await host.evaluate('networkFault.blackhole=false')
            await host.wait_for_function("() => document.getElementById('hostStartButton').textContent==='ПРОДОЛЖИТЬ' && !document.getElementById('hostStartButton').disabled",timeout=7000)
            assert await host.locator('#roomCode').inner_text()==code
            await host.locator('#hostStartButton').click()
            for page in (host,guest):
                await page.wait_for_function("() => document.getElementById('onlineRoom').hidden && !isPaused")
            assert len(sockets)==3,sockets
            assert not errors,errors
            print('PASS stalled connection detected, same host seat restored automatically, match resumed')
        finally:
            await browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('origin',nargs='?',default='http://127.0.0.1:8000')
    parser.add_argument('--chromium')
    args=parser.parse_args()
    asyncio.run(check(args.origin.rstrip('/'),args.chromium))
