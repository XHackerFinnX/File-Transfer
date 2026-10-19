"""Deterministic prediction checks at different refresh rates and latencies."""

import argparse
import asyncio

from playwright.async_api import async_playwright

SIMULATION = r"""async () => {
  const { PlayerPrediction } = await import('/volleyball/online/shared/prediction.js');
  const { Match, DT } = await import('/volleyball/online/shared/physics.js');
  const results = [];
  for (const index of [0, 1]) for (const fps of [60, 120, 144]) for (const rtt of [0, 100, 200]) {
    const match = new Match({speed1:25,speed2:25}); match.start();
    const prediction = new PlayerPrediction(), pending = [], snapshots = [], rendered = [];
    const direction = index === 0 ? 1 : -1;
    let seq = 0, mask = 0, acknowledged = -1, serverMask = 0, step = DT, frame = 0, count = 0, delivery = 0;
    prediction.receive(match.snapshot(),index,match.settings,{seq:-1,mask:0},0,rtt);
    for (let now=0; now<=1000; now++) {
      if (now===100) mask=index===0?2:1;
      if (now===550) mask=0;
      if (now%33===0 || now===100 || now===550) {
        prediction.input(seq,mask,now);
        pending.push({at:now+rtt/2,seq:seq++,mask});
      }
      while(pending.length && pending[0].at<=now) {
        const input=pending.shift(); acknowledged=input.seq;serverMask=input.mask;
      }
      if(now>=step) {
        const inputs=[0,0];inputs[index]=serverMask; match.step(inputs);step+=DT;
        if(++count%2===0) {
          const jitter=[-8,6,-3,9][count/2%4];
          delivery=Math.max(now+Math.max(0,rtt/2+jitter),delivery+1);
          snapshots.push({at:delivery,state:match.snapshot(),seq:acknowledged,mask:serverMask});
        }
      }
      while(snapshots.length && snapshots[0].at<=now) {
        const snapshot=snapshots.shift();
        prediction.receive(snapshot.state,index,match.settings,snapshot,now,rtt);
      }
      if(now>=frame) {
        const pose=prediction.render(now,mask); frame+=1000/fps;
        if(now>=100) rendered.push({at:now,x:pose.x});
      }
    }
    const deltas=rendered.slice(1).map((p,i)=>(p.x-rendered[i].x)*direction);
    const backwards=Math.max(0,-Math.min(...deltas));
    const first=rendered.find(p=>(p.x-(index===0?275:825))*direction>1);
    results.push({role:index===0?'host':'guest',fps,rtt,backwards,firstMotion:first?.at});
    if(backwards>.5) throw Error('Prediction reverses on snapshots or release: '+JSON.stringify(results.at(-1)));
    if(!first || first.at>150) throw Error('Local input waits for network: '+JSON.stringify(results.at(-1)));
    if(rendered.some(p=>!Number.isFinite(p.x)))throw Error('Non-finite render');
    const state=match.snapshot();state.phase='paused';
    prediction.receive(state,index,match.settings,{seq:acknowledged,mask:0},1001,rtt);
    const paused=prediction.render(1001,0).x;
    if(prediction.render(1050,0).x!==paused)throw Error('Paused prediction drifts');
    state.phase='playing';state.round++;state.players[index].x=index===0?275:825;
    state.players[index].vx=state.players[index].vy=0;
    prediction.receive(state,index,match.settings,{seq:acknowledged,mask:0},1060,rtt);
    if(Math.abs(prediction.render(1060,0).x-state.players[index].x)>.001)throw Error('Round reset is blended');
    const held=index===0?2:1;
    prediction.input(seq+1,held,1100);
    for(let at=1100;at<3100;at+=1000/fps) {
      const pose=prediction.render(at,held), middle=match.settings.courtWidth/2;
      const low=index===0?45:middle+45,high=index===0?middle-45:match.settings.courtWidth-45;
      if(pose.x<low || pose.x>high || pose.y>585)throw Error('Prediction crosses the court boundary');
    }
  }
  return results;
}"""


async def check(origin, chromium):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=chromium, headless=True, args=['--no-sandbox'])
        try:
            page = await browser.new_page()
            await page.goto(origin+'/volleyball/online/')
            results = await page.evaluate(SIMULATION)
            print(f"PASS prediction: {len(results)} scenarios, both roles, 60/120/144 Hz and 0/100/200 ms RTT")
            print(f"Maximum backwards correction: {max(r['backwards'] for r in results):.3f} game pixels; immediate local response, pause and round reset checked")
        finally:
            await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('origin', nargs='?', default='http://127.0.0.1:8000')
    parser.add_argument('--chromium')
    args = parser.parse_args()
    asyncio.run(check(args.origin.rstrip('/'), args.chromium))
