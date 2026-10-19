import { movePlayer, DT, GROUND } from './physics.js';

const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const clone = player => ({ ...player, sVelX: 0, sVelY: 0 });

// Only the local avatar is predicted. Ball collisions, points and rounds always
// come from the server. Keep the render position continuous while reconciling
// acknowledged inputs; remote players use the separate snapshot buffer.
export class PlayerPrediction {
  constructor() { this.reset(); }

  reset() {
    this.player = null;
    this.history = [];
    this.offset = { x: 0, y: 0 };
    this.lastFrame = null;
    this.signature = '';
  }

  input(seq, mask, at) {
    this.history.push({ seq, mask, at });
    if (this.history.length > 120) this.history.shift();
  }

  receive(state, index, settings, ack, now, rtt) {
    const signature = JSON.stringify([state.round, state.phase, settings]);
    const discontinuity = signature !== this.signature;
    const previous = this.player && !discontinuity ? this.render(now, this.mask) : null;
    this.signature = signature;
    this.index = index;
    this.settings = settings;
    this.active = state.phase === 'playing';
    this.player = clone(state.players[index]);
    this.mask = ack?.mask ?? 0;
    this.history = this.history.filter(input => input.seq > (ack?.seq ?? -1) && now-input.at < 1000);

    // A snapshot is approximately half an RTT old. Replay pending commands in
    // timestamp order instead of applying a newly pressed key to the whole RTT.
    const age = this.active ? clamp(rtt/2, 0, 150) : 0;
    this.time = now-age;
    this.remainder = 0;
    this.advance(now, this.mask);
    this.offset = { x: 0, y: 0 };
    const target = this.pose(this.mask);
    if (previous && Math.hypot(previous.x-target.x, previous.y-target.y) < 120) {
      this.offset.x = previous.x-target.x;
      this.offset.y = previous.y-target.y;
    }
    this.lastFrame = now;
  }

  maskAt(at, fallback) {
    let mask = fallback;
    for (const input of this.history) {
      if (input.at > at) break;
      mask = input.mask;
    }
    return mask;
  }

  advance(now, fallback) {
    const elapsed = clamp(now-this.time, 0, 100);
    this.time = now;
    if (!this.active) return;
    const tempo = this.settings.tempo/100;
    this.remainder += elapsed*tempo;
    while (this.remainder >= DT) {
      const at = now-(this.remainder-DT)/tempo;
      this.mask = this.maskAt(at, fallback);
      movePlayer(this.player, this.mask, this.index, this.settings);
      this.remainder -= DT;
    }
  }

  pose(mask) {
    const result = { ...this.player };
    if (this.active) {
      const next = clone(this.player);
      movePlayer(next, mask, this.index, this.settings);
      const alpha = this.remainder/DT;
      for (const key of ['x', 'y']) result[key] += (next[key]-result[key])*alpha;
    }
    return result;
  }

  render(now, mask) {
    if (!this.player) return null;
    const elapsed = this.lastFrame === null ? 0 : clamp(now-this.lastFrame, 0, 100);
    this.lastFrame = now;
    this.advance(now, mask);
    const decay = Math.exp(-elapsed/90);
    this.offset.x *= decay;
    this.offset.y *= decay;
    const result = this.pose(mask);
    result.x += this.offset.x;
    result.y += this.offset.y;
    const middle = this.settings.courtWidth/2, radius = result.radius;
    result.x = clamp(result.x, this.index === 0 ? radius : middle+radius,
      this.index === 0 ? middle-radius : this.settings.courtWidth-radius);
    result.y = Math.min(GROUND, result.y);
    return result;
  }
}
