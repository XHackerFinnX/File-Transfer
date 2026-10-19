"""Authoritative Python port of volleyball-online's shared physics/protocol v1."""

import math

GROUND = 585
DT = 1000 / 60
# key: (minimum, maximum, step, default), matching browser shared/settings.js.
PARAMS = {
    "netHeight": (100, 460, .5, 322.5),
    "netWidth": (4, 24, 1, 8),
    "courtWidth": (900, 1600, 50, 1100),
    "serveHeight": (180, 420, 5, 280),
    "speed1": (25, 250, 5, 100), "speed2": (25, 250, 5, 100),
    "jump1": (50, 180, 5, 100), "jump2": (50, 180, 5, 100),
    "acceleration": (50, 200, 5, 100),
    "ballSpeed": (50, 200, 5, 100), "ballSize": (50, 150, 5, 100),
    "hitPower": (50, 200, 5, 100), "gravity": (30, 180, 5, 100),
    "airDrag": (0, 3, .1, 1), "tempo": (25, 200, 5, 100),
    "winScore": (1, 50, 1, 5),
}
BOOLEAN_SETTINGS = {"winByTwo", "hellUnlimited", "particles", "trail", "shake"}
DEFAULT_SETTINGS = {key: spec[3] for key, spec in PARAMS.items()} | {
    "maxTouches": 3, "winByTwo": False, "hellUnlimited": True,
    "particles": True, "trail": True, "shake": True,
}


def integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def validate_settings(raw):
    if not isinstance(raw, dict) or raw.keys() - DEFAULT_SETTINGS.keys():
        raise ValueError("INVALID_SETTINGS")
    for key, value in raw.items():
        if key in PARAMS:
            low, high, step, _ = PARAMS[key]
            if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
                raise ValueError("INVALID_SETTINGS")
            steps = (value - low) / step
            if abs(steps - round(steps)) > 1e-6:
                raise ValueError("INVALID_SETTINGS")
        elif key in BOOLEAN_SETTINGS:
            if type(value) is not bool:
                raise ValueError("INVALID_SETTINGS")
        elif not integer(value, 0, 10):
            raise ValueError("INVALID_SETTINGS")
    return DEFAULT_SETTINGS | raw


def clamp(value, low, high):
    return max(low, min(high, value))


def shape():
    return dict(scaleX=1, scaleY=1, sVelX=0, sVelY=0)


def jelly(p, x, y):
    p.update(scaleX=x, scaleY=y, sVelX=(1-x)*.2, sVelY=(1-y)*.2)


def animate(p):
    for axis in ("X", "Y"):
        scale, velocity = "scale" + axis, "sVel" + axis
        p[velocity] = (p[velocity] + (1-p[scale])*.15)*.8
        p[scale] += p[velocity]


def move_player(p, mask, index, settings):
    left, right, jump, down = (bool(mask & bit) for bit in (1, 2, 4, 8))
    speed = settings["speed1" if index == 0 else "speed2"] / 100
    accel = 1.8*settings["acceleration"]/100*speed
    if left:
        p["vx"] -= accel
    if right:
        p["vx"] += accel
    if not left and not right:
        p["vx"] *= .85
    if jump and p["y"] == GROUND:
        p["vy"] = p["baseJump"]
        jelly(p, .6, 1.4)
    if not jump and p["vy"] < -5:
        p["vy"] *= .8
    if down and not jump and p["y"] < GROUND:
        p["vy"] += 2.5
    p["vx"] = clamp(p["vx"], -12*speed, 12*speed)
    airborne = p["y"] < GROUND
    p["vy"] += .45*settings["gravity"]/100
    p["x"] += p["vx"]
    p["y"] += p["vy"]
    if p["y"] >= GROUND:
        if airborne:
            jelly(p, 1.4, .6)
        p.update(y=GROUND, vy=0)
    middle, radius = settings["courtWidth"]/2, p["radius"]
    if index == 0:
        if p["x"] < radius:
            p["x"], p["vx"] = radius, p["vx"]*-.5
        if p["x"] > middle-radius:
            p["x"], p["vx"] = middle-radius, 0
    else:
        if p["x"] < middle+radius:
            p["x"], p["vx"] = middle+radius, 0
        if p["x"] > settings["courtWidth"]-radius:
            p["x"], p["vx"] = settings["courtWidth"]-radius, p["vx"]*-.5


class Match:
    def __init__(self, settings=None):
        self.settings = validate_settings(settings or {})
        self.phase, self.score = "lobby", [0, 0]
        self.tick = self.time = self.round = self.event_id = self.accumulator = 0
        self.events = []
        self.target = self.settings["winScore"]
        self.players = [dict(x=0, y=GROUND, vx=0, vy=0, radius=45, baseJump=-16, hitTimer=0, **shape()) for _ in range(2)]
        self.ball = dict(x=0, y=0, vx=0, vy=0, radius=40, **shape())
        self.net = dict(x=0, y=0, height=0, width=8, bend=0, bendVel=0)
        self.configure()
        self.reset_positions()
        self.reset_round(1)

    def configure(self):
        s = self.settings
        self.gravity, self.drag = .45*s["gravity"]/100, 1-s["airDrag"]/100
        self.players[0]["baseJump"] = -16*s["jump1"]/100
        self.players[1]["baseJump"] = -16*s["jump2"]/100
        self.ball["radius"] = 40*s["ballSize"]/100
        self.net.update(x=s["courtWidth"]/2, y=GROUND-s["netHeight"], height=s["netHeight"], width=s["netWidth"])

    def reset_positions(self):
        for i, p in enumerate(self.players):
            p.update(x=self.settings["courtWidth"]*(.25 if i == 0 else .75), y=GROUND, vx=0, vy=0, hitTimer=0, **shape())
        self.ball.update(shape())

    def reset_round(self, server):
        self.server, self.serving = server, True
        self.possession = self.touches = self.cooldown = self.combo = 0
        self.round += 1
        self.net.update(bend=0, bendVel=0)
        self.ball.update(vx=0, vy=0)
        p = self.players[server-1]
        reach = p["radius"]*2.8+self.ball["radius"]+p["baseJump"]**2/(2*self.gravity)*.65
        self.ball["x"] = self.settings["courtWidth"]*(.25 if server == 1 else .75)
        self.ball["y"] = GROUND-min(self.settings["serveHeight"], reach, GROUND-self.ball["radius"]-35)

    def start(self):
        self.score, self.target, self.phase, self.accumulator = [0, 0], self.settings["winScore"], "playing", 0
        self.configure()
        self.reset_positions()
        self.reset_round(1)
        self.emit("start")

    def apply_settings(self, settings):
        self.settings = validate_settings(settings)
        self.target = self.settings["winScore"]
        self.configure()
        self.reset_positions()
        self.reset_round(self.server)
        if self.phase != "lobby" and self.has_winner():
            self.phase = "finished"
            self.emit("win", winner=1 if self.score[0] > self.score[1] else 2)

    def extend(self):
        self.target = max(self.target, *self.score)+5
        self.phase = "playing"
        self.reset_positions()
        self.reset_round(1)
        self.emit("start")

    def has_winner(self):
        return max(self.score) >= self.target and (not self.settings["winByTwo"] or abs(self.score[0]-self.score[1]) >= 2)

    def emit(self, kind, **data):
        self.event_id += 1
        self.events.append(dict(id=self.event_id, kind=kind, **data))
        self.events = self.events[-12:]

    def point(self, winner, reason="floor"):
        self.score[winner-1] += 1
        self.emit("point", winner=winner, reason=reason, x=self.ball["x"], y=GROUND)
        if self.has_winner():
            self.phase = "finished"
            self.emit("win", winner=winner)
        else:
            self.reset_round(winner)

    def advance(self, elapsed, inputs):
        if self.phase != "playing":
            self.accumulator = 0
            return
        self.accumulator += min(100, max(0, elapsed))*self.settings["tempo"]/100
        steps = 0
        while self.accumulator >= DT and steps < 12 and self.phase == "playing":
            self.step(inputs)
            self.accumulator -= DT
            steps += 1

    def move_ball(self):
        if self.serving:
            return
        b, n, s = self.ball, self.net, self.settings
        ball_dt = s["ballSpeed"]/100
        steps = min(40, max(1, math.ceil(math.hypot(b["vx"], b["vy"])*ball_dt/max(4, min(b["radius"]/2, n["width"])))))
        dt = ball_dt/steps
        for _ in range(steps):
            b["vx"] *= self.drag**dt
            b["vy"] = b["vy"]*self.drag**dt+self.gravity*dt
            limit = 20*max(1, s["hitPower"]/100)
            b["vy"] = clamp(b["vy"], -limit, limit)
            old_x, old_y = b["x"], b["y"]
            b["x"] += b["vx"]*dt
            b["y"] += b["vy"]*dt
            if b["x"] < b["radius"]:
                b["x"], b["vx"] = b["radius"], abs(b["vx"])
                jelly(b, .63, 1.37)
            if b["x"] > s["courtWidth"]-b["radius"]:
                b["x"], b["vx"] = s["courtWidth"]-b["radius"], -abs(b["vx"])
                jelly(b, .63, 1.37)
            cx, cy = clamp(b["x"], n["x"]-n["width"], n["x"]+n["width"]), clamp(b["y"], n["y"], GROUND)
            dx, dy = b["x"]-cx, b["y"]-cy
            distance = math.hypot(dx, dy)
            if distance < b["radius"]:
                if distance < .001:
                    dx, dy = (0, -1) if old_y < n["y"] else (-1 if old_x < n["x"] else 1, 0)
                    distance = 1
                nx, ny = dx/distance, dy/distance
                b["x"], b["y"] = cx+nx*(b["radius"]+.1), cy+ny*(b["radius"]+.1)
                approach = b["vx"]*nx+b["vy"]*ny
                if approach < 0:
                    restitution = .8 if ny < -.5 else .7
                    b["vx"] -= (1+restitution)*approach*nx
                    b["vy"] -= (1+restitution)*approach*ny
                    n["bendVel"] = clamp(-approach*nx, -25, 25)
                    jelly(b, 1.37 if ny < -.5 else .72, .63 if ny < -.5 else 1.28)
                if ny < -.5 and abs(b["vx"]) < 1.5:
                    b["vx"] += 3 if b["x"] >= n["x"] else -3

    def step(self, inputs=(0, 0)):
        if self.phase != "playing":
            return
        self.tick += 1
        self.time += .05
        self.cooldown = max(0, self.cooldown-1)
        animate(self.ball)
        for p in self.players:
            animate(p)
            p["hitTimer"] = max(0, p["hitTimer"]-1)
        n = self.net
        n["bendVel"] = (n["bendVel"]-n["bend"]*.1)*.85
        n["bend"] += n["bendVel"]
        for i, p in enumerate(self.players):
            move_player(p, inputs[i], i, self.settings)
        self.move_ball()
        b = self.ball
        for i, p in enumerate(self.players):
            top, bottom = p["y"]-p["radius"]*2.8*p["scaleY"], p["y"]-p["radius"]*.5
            closest_y = clamp(b["y"], top, bottom)
            dx, dy = b["x"]-p["x"], b["y"]-closest_y
            combined = b["radius"]+p["radius"]*p["scaleX"]*1.1
            if dx*dx+dy*dy >= combined*combined:
                continue
            self.serving = False
            distance = math.hypot(dx, dy)
            if distance == 0:
                dx, dy, distance = 0, -1, 1
            nx, ny, power = dx/distance, dy/distance, self.settings["hitPower"]/100
            bounce = min(math.hypot(b["vx"], b["vy"])*.8+10, 18)*power
            b.update(vx=nx*bounce+p["vx"]*.5, vy=ny*bounce-(9 if closest_y < p["y"]-p["radius"] else 4)*power,
                     x=p["x"]+nx*(combined+.1), y=closest_y+ny*(combined+.1))
            p["hitTimer"] = 15
            jelly(b, 1.37, .63)
            jelly(p, 1.2, .8)
            if self.cooldown == 0:
                self.combo += 1
                if self.possession != i+1:
                    self.possession, self.touches = i+1, 1
                else:
                    self.touches += 1
                self.emit("hit", player=i+1, x=b["x"], y=b["y"], touches=self.touches, combo=self.combo)
                if self.settings["maxTouches"] != 0 and self.touches > self.settings["maxTouches"]:
                    self.point(2 if i == 0 else 1, "touches")
                    return
                self.cooldown = 15
        if b["y"]+b["radius"] > GROUND:
            self.point(2 if b["x"] < self.settings["courtWidth"]/2 else 1)

    def snapshot(self):
        def pick(p):
            return {key: round(p[key], 3) for key in ("x", "y", "vx", "vy", "radius", "baseJump", "scaleX", "scaleY", "hitTimer") if key in p}
        return dict(tick=self.tick, time=round(self.time, 3), round=self.round, phase=self.phase, score=self.score[:],
                    target=self.target, serving=self.serving, server=self.server, touches=self.touches, possession=self.possession,
                    players=[pick(p) for p in self.players], ball=pick(self.ball), bend=round(self.net["bend"], 3), events=self.events[:])
