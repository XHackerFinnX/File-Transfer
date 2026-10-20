import { DT, GROUND } from './physics.js';

const clamp = (v, low, high) => Math.max(low, Math.min(high, v));
const copy = state => ({ ...state, players: state.players.map(p => ({ ...p })), ball: { ...state.ball } });

// Render on the server's monotonic timeline. Packet arrival times can bunch up
// after a Wi-Fi/TCP stall and must not be used as the simulation clock.
export class SnapshotBuffer {
  constructor() { this.reset(); }
  reset() {
    this.items = []; this.offsets = []; this.clockOffset = 0; this.hasClock = false;
    this.jitter = 0; this.delay = 45; this.cursor = null; this.frameAt = null;
  }
  push(state, seq, received, serverTime, inputs) {
    const stamped = Number.isFinite(serverTime);
    if (stamped && !this.hasClock) this.reset();
    this.hasClock = stamped || this.hasClock;
    const time = stamped ? serverTime : received;
    const previous = this.latest;
    if (stamped) {
      this.offsets.push(received-time);
      if (this.offsets.length > 120) this.offsets.shift();
      this.clockOffset = Math.min(...this.offsets);
    }
    if (previous) {
      const deviation = Math.abs((received-previous.received)-(time-previous.time));
      this.jitter += (deviation-this.jitter)*.15;
      const target = clamp(45+this.jitter*2, 45, 140);
      this.delay = target > this.delay ? target : this.delay+(target-this.delay)*.03;
    }
    const discontinuity = previous && (previous.state.round !== state.round || previous.state.phase !== state.phase);
    this.items.push({ state, seq, received, time, inputs });
    if (this.items.length > 30) this.items.shift();
    if (discontinuity) { this.cursor = null; this.frameAt = null; }
  }
  get latest() { return this.items.at(-1); }
  age(now, rtt) {
    return this.latest ? Math.max(0, now-this.latest.time-this.clockOffset+rtt/2) : 0;
  }
  sample(now, settings) {
    const latest = this.latest;
    if (!latest) return null;
    const target = now-this.clockOffset-this.delay;
    if (this.cursor === null) this.cursor = target;
    else {
      const elapsed = clamp(now-this.frameAt, 0, 100);
      this.cursor += elapsed*clamp(1+(target-this.cursor)/350, .85, 1.15);
    }
    this.frameAt = now;
    // Bounded visual extrapolation covers a single delayed snapshot. During a
    // real outage stop guessing instead of running avatars across the court.
    this.cursor = Math.min(this.cursor, latest.time+50);
    const result = copy(latest.state);
    if (latest.state.phase !== 'playing') return result;
    const first = this.items.findIndex(item => item.state.round===latest.state.round && item.state.phase===latest.state.phase);
    let a = this.items[first], b = a;
    for (let i=first; i<this.items.length-1; i++) {
      if (this.items[i].time <= this.cursor) { a=this.items[i]; b=this.items[i+1]; }
    }
    if (this.cursor > latest.time) {
      const steps = Math.min(50, this.cursor-latest.time)/DT*settings.tempo/100;
      for (let i=0; i<2; i++) {
        const p=result.players[i], middle=settings.courtWidth/2;
        p.x=clamp(p.x+p.vx*steps, i===0?p.radius:middle+p.radius, i===0?middle-p.radius:settings.courtWidth-p.radius);
        p.y=Math.min(GROUND, p.y+p.vy*steps);
      }
      const ballSteps=steps*settings.ballSpeed/100;
      result.ball.x=clamp(result.ball.x+result.ball.vx*ballSteps, result.ball.radius, settings.courtWidth-result.ball.radius);
      result.ball.y=Math.min(GROUND-result.ball.radius, result.ball.y+result.ball.vy*ballSteps);
      result.time+=steps*.05;
    } else if (a.state.round===latest.state.round && b.state.round===latest.state.round && b.time>a.time) {
      const t=clamp((this.cursor-a.time)/(b.time-a.time),0,1);
      for (let i=0; i<2; i++) for (const key of ['x','y','vx','vy','scaleX','scaleY'])
        result.players[i][key]=a.state.players[i][key]+(b.state.players[i][key]-a.state.players[i][key])*t;
      for (const key of ['x','y','scaleX','scaleY']) result.ball[key]=a.state.ball[key]+(b.state.ball[key]-a.state.ball[key])*t;
      result.time=a.state.time+(b.state.time-a.state.time)*t;
      result.bend=a.state.bend+(b.state.bend-a.state.bend)*t;
    } else {
      result.players=a.state.players.map(p=>({...p}));result.ball={...a.state.ball};
      result.time=a.state.time;result.bend=a.state.bend;
    }
    // Neither interpolation across an impact nor short visual extrapolation may
    // draw the ball through the post. Physics remains authoritative on the server.
    const origin=this.cursor>latest.time?latest.state.ball:a.state.ball;
    if(!result.serving)globalThis.VolleyNet.resolve(result.ball,
      {x:settings.courtWidth/2,y:GROUND-settings.netHeight,width:settings.netWidth},origin.x,origin.y);
    return result;
  }
}
