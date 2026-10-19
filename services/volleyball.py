"""In-process, authoritative volleyball rooms for FastAPI (one worker)."""

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

from config import config
from services.volleyball_physics import Match, integer, validate_settings

GAME_ROOT = Path(__file__).resolve().parents[1] / "games" / "volleyball"
logger = logging.getLogger(__name__)
CODE = re.compile(r"[1-9][0-9]{3}\Z")
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
SNAPSHOT = object()


class Bucket:
    def __init__(self, capacity, period, now=None):
        self.capacity, self.period = capacity, period
        self.tokens, self.updated = capacity, time.monotonic() if now is None else now

    def take(self, now=None):
        now = time.monotonic() if now is None else now
        self.tokens = min(self.capacity, self.tokens+max(0, now-self.updated)*self.capacity/self.period)
        self.updated = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class Limits:
    def __init__(self):
        self.entries = {}

    def sweep(self, now):
        for key, bucket in list(self.entries.items()):
            if now-bucket.updated > max(bucket.period*2, 600):
                del self.entries[key]

    def take(self, key, capacity, period, now):
        if key not in self.entries:
            if len(self.entries) >= 5000:
                self.sweep(now)
                if len(self.entries) >= 5000:
                    return False
            self.entries[key] = Bucket(capacity, period, now)
        return self.entries[key].take(now)


@dataclass(eq=False)
class Connection:
    websocket: WebSocket
    ip: str
    seat: "Seat | None" = None
    strikes: int = 0
    closing: bool = False
    joined_at: float = field(default_factory=time.monotonic)
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=16))
    messages: Bucket = field(default_factory=lambda: Bucket(120, 1))
    controls: Bucket = field(default_factory=lambda: Bucket(12, 10))
    latest_state: str | None = None
    state_queued: bool = False
    probe_id: int | None = None
    probe_started: float = 0
    rtt_ms: float | None = None
    rtt_at: float = 0


@dataclass
class Seat:
    index: int
    room: "Room"
    token_hash: bytes
    conn: Connection | None
    color: int
    mask: int = 0
    last_input: float = field(default_factory=time.monotonic)
    last_seq: int = -1
    disconnected_at: float = 0


@dataclass
class Room:
    code: str
    owner_ip: str
    born: float = field(default_factory=time.monotonic)
    activity: float = field(default_factory=time.monotonic)
    seats: list = field(default_factory=lambda: [None, None])
    match: Match = field(default_factory=Match)
    reason: str = "waiting"
    revision: int = 0
    seq: int = 0

    def public(self):
        return dict(code=self.code, revision=self.revision, phase=self.match.phase, reason=self.reason,
                    settings=self.match.settings,
                    players=[dict(role="host" if i == 0 else "guest", color=s.color, connected=s.conn is not None)
                             if s else None for i, s in enumerate(self.seats)])

    def both_connected(self):
        return all(s and s.conn and not s.conn.closing for s in self.seats)

    def stop_inputs(self):
        for seat in self.seats:
            if seat:
                seat.mask = 0


class VolleyballServer:
    def __init__(self):
        self.rooms: dict[str, Room] = {}
        self.connections: set[Connection] = set()
        self.limits = Limits()
        self.upgrades = Bucket(240, 60)
        self.task: asyncio.Task | None = None
        self.grace_seconds = 45
        self.idle_seconds = 900
        self.max_age_seconds = 7200
        self.server_lag_ms = 0

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    async def start(self):
        if not self.running:
            self.task = asyncio.create_task(self.run(), name="volleyball-simulation")

    async def stop(self):
        for conn in list(self.connections):
            self.close(conn, 1012, "SERVER_RESTART")
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        self.rooms.clear()
        self.connections.clear()
        self.limits = Limits()
        self.upgrades = Bucket(240, 60)

    def close(self, conn, code=1000, reason=""):
        if conn.closing:
            return
        conn.closing = True
        if conn.queue.full():
            # A slow client must not delay physics or other clients.
            while not conn.queue.empty():
                conn.queue.get_nowait()
        conn.queue.put_nowait((code, reason))

    def queue_text(self, conn, text, latest=False):
        if conn.closing:
            return
        if latest:
            conn.latest_state = text
            if conn.state_queued:
                return
            conn.state_queued = True
        try:
            conn.queue.put_nowait(SNAPSHOT if latest else text)
        except asyncio.QueueFull:
            self.close(conn, 1013, "SLOW_CLIENT")

    def send(self, conn, packet):
        self.queue_text(conn, json.dumps(packet, separators=(",", ":"), allow_nan=False))

    def broadcast(self, room, packet, latest=False):
        text = json.dumps(packet, separators=(",", ":"), allow_nan=False)
        for seat in room.seats:
            if seat and seat.conn:
                self.queue_text(seat.conn, text, latest=latest)

    def broadcast_room(self, room):
        self.broadcast(room, dict(type="room", room=room.public()))

    def broadcast_state(self, room):
        room.seq += 1
        now = time.monotonic()
        inputs = [dict(seq=s.last_seq, mask=s.mask if s.conn and now-s.last_input <= .3 else 0)
                  if s else dict(seq=-1, mask=0) for s in room.seats]
        self.broadcast(room, dict(type="state", seq=room.seq, revision=room.revision,
                                  serverTime=now*1000, inputs=inputs, state=room.match.snapshot()), latest=True)

    def broadcast_network(self, room, now):
        # RTT is timed by the server, never accepted as a client-provided value.
        pings = [round(s.conn.rtt_ms) if s and s.conn and s.conn.rtt_ms is not None
                 and now-s.conn.rtt_at < 5 else None for s in room.seats]
        self.broadcast(room, dict(type="network", rtt=pings, serverLag=round(self.server_lag_ms, 1)))

    def error(self, conn, code):
        self.send(conn, dict(type="error", code=code))

    def protocol_error(self, conn):
        conn.strikes += 1
        self.error(conn, "INVALID_MESSAGE")
        if conn.strikes >= 3:
            self.close(conn, 1008, "INVALID_MESSAGE")

    def close_room(self, room, reason):
        if self.rooms.pop(room.code, None) is None:
            return
        room.stop_inputs()
        for seat in room.seats:
            if seat and seat.conn:
                conn = seat.conn
                conn.seat = None
                self.send(conn, dict(type="closed", code=reason))
                self.close(conn, 1000, "ROOM_CLOSED")

    def remove_guest(self, room, reason="PLAYER_LEFT"):
        guest = room.seats[1]
        if guest and guest.conn:
            conn = guest.conn
            conn.seat = None
            self.send(conn, dict(type="closed", code=reason))
            self.close(conn, 1000, "PLAYER_LEFT")
        room.seats[1], room.match.phase, room.reason = None, "lobby", "waiting"
        room.revision += 1
        room.stop_inputs()
        self.broadcast_room(room)
        self.broadcast_state(room)

    def joined(self, conn, token):
        seat, room = conn.seat, conn.seat.room
        room.activity = time.monotonic()
        room.revision += 1
        self.send(conn, dict(type="joined", code=room.code, role="host" if seat.index == 0 else "guest",
                             token=token, room=room.public(), serverTime=time.monotonic()*1000, state=room.match.snapshot()))
        self.broadcast_room(room)
        self.broadcast_state(room)

    def attach(self, conn, room, index, color):
        token = secrets.token_urlsafe(32)
        seat = Seat(index, room, hashlib.sha256(token.encode()).digest(), conn, color)
        conn.seat = room.seats[index] = seat
        self.joined(conn, token)

    def disconnect(self, conn):
        self.connections.discard(conn)
        seat = conn.seat
        if not seat or seat.conn is not conn:
            return
        seat.conn, seat.mask, seat.disconnected_at = None, 0, time.monotonic()
        room = seat.room
        room.stop_inputs()
        if room.match.phase == "playing":
            room.match.phase, room.reason = "paused", "connection"
        room.revision += 1
        self.broadcast_room(room)
        self.broadcast_state(room)

    def require_member(self, conn, host=False):
        if not conn.seat:
            self.error(conn, "NOT_IN_ROOM")
            return None
        if host and conn.seat.index != 0:
            self.error(conn, "NOT_HOST")
            return None
        return conn.seat

    def handle(self, conn, msg):
        now = time.monotonic()
        if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
            self.protocol_error(conn)
            return
        kind = msg["type"]
        fields = {
            "ping": {"id"}, "pong_ack": {"id"}, "input": {"seq", "mask"}, "create": {"color"},
            "join": {"code", "color"}, "resume": {"code", "token"}, "color": {"color"},
            "leave": set(), "settings": {"settings"}, "start": set(), "restart": set(),
            "pause": {"paused"}, "extend": set(), "kick": set(),
        }
        if kind not in fields or msg.keys() != fields[kind] | {"type"}:
            self.protocol_error(conn)
            return
        valid = True
        if kind in {"ping", "pong_ack"}:
            valid = integer(msg["id"], 0, 2147483647)
        elif kind == "input":
            valid = integer(msg["seq"], 0, 2147483647) and integer(msg["mask"], 0, 15)
        elif kind in {"create", "join", "color"}:
            valid = integer(msg["color"], 0, 4)
        if kind in {"join", "resume"}:
            valid = valid and isinstance(msg["code"], str) and CODE.fullmatch(msg["code"])
        if kind == "resume":
            valid = valid and isinstance(msg["token"], str) and TOKEN.fullmatch(msg["token"])
        if kind == "pause":
            valid = type(msg["paused"]) is bool
        if not valid:
            self.protocol_error(conn)
            return
        if kind == "ping":
            conn.probe_id, conn.probe_started = msg["id"], now
            self.send(conn, dict(type="pong", id=msg["id"], probe=True))
            return
        if kind == "pong_ack":
            if conn.probe_id == msg["id"]:
                sample = (now-conn.probe_started)*1000
                conn.rtt_ms = sample if conn.rtt_ms is None else conn.rtt_ms*.7+sample*.3
                conn.rtt_at, conn.probe_id = now, None
            return
        if kind == "input":
            seat = self.require_member(conn)
            if seat and msg["seq"] > seat.last_seq:
                seat.last_seq, seat.mask, seat.last_input = msg["seq"], msg["mask"], now
                if seat.mask:
                    seat.room.activity = now
            return
        if not conn.controls.take(now):
            self.error(conn, "RATE_LIMIT")
            return
        if kind in {"create", "join", "resume"}:
            if conn.seat:
                self.error(conn, "ALREADY_IN_ROOM")
                return
            capacity = {"create": 3, "join": 8, "resume": 15}[kind]
            if not self.limits.take((kind, conn.ip), capacity, 60, now):
                self.error(conn, "RATE_LIMIT")
                return
            if kind == "create":
                if len(self.rooms) >= config.VOLLEYBALL_MAX_ROOMS or sum(r.owner_ip == conn.ip for r in self.rooms.values()) >= 3:
                    self.error(conn, "SERVER_FULL")
                    return
                for _ in range(200):
                    code = str(1000+secrets.randbelow(9000))
                    if code not in self.rooms:
                        break
                else:
                    self.error(conn, "SERVER_FULL")
                    return
                room = Room(code, conn.ip)
                self.rooms[code] = room
                self.attach(conn, room, 0, msg["color"])
            elif kind == "join":
                room = self.rooms.get(msg["code"])
                if not room or room.seats[1] or not room.seats[0].conn or room.match.phase != "lobby":
                    self.error(conn, "ROOM_UNAVAILABLE")
                    return
                self.attach(conn, room, 1, msg["color"])
                room.reason = ""
                self.broadcast_room(room)
            else:
                room = self.rooms.get(msg["code"])
                digest = hashlib.sha256(msg["token"].encode()).digest()
                seat = next((s for s in room.seats if s and hmac.compare_digest(s.token_hash, digest)), None) if room else None
                if not seat or (not seat.conn and now-seat.disconnected_at > self.grace_seconds):
                    self.error(conn, "SESSION_EXPIRED")
                    return
                if seat.conn:
                    previous = seat.conn
                    previous.seat = None
                    self.close(previous, 4001, "SESSION_REPLACED")
                seat.conn, conn.seat = conn, seat
                seat.mask, seat.last_seq, seat.last_input, seat.disconnected_at = 0, -1, now, 0
                self.joined(conn, msg["token"])
            return
        seat = self.require_member(conn, host=kind in {"settings", "start", "restart", "pause", "extend", "kick"})
        if not seat:
            return
        room = seat.room
        if kind == "color":
            seat.color = msg["color"]
            room.activity = now
            room.revision += 1
            self.broadcast_room(room)
            return
        if kind == "leave":
            if seat.index == 0:
                self.close_room(room, "HOST_LEFT")
            else:
                self.remove_guest(room)
            return
        if kind == "settings":
            try:
                settings = validate_settings(msg["settings"])
            except ValueError:
                self.error(conn, "INVALID_SETTINGS")
                return
            room.match.apply_settings(settings)
            room.revision += 1
            room.stop_inputs()
        elif kind == "pause":
            if room.match.phase not in {"playing", "paused"}:
                self.error(conn, "INVALID_STATE")
                return
            if not msg["paused"] and not room.both_connected():
                self.error(conn, "WAITING_PLAYER")
                return
            room.match.phase = "paused" if msg["paused"] else "playing"
            room.reason = "host" if msg["paused"] else ""
            room.stop_inputs()
        elif kind == "kick":
            self.remove_guest(room, "KICKED")
            return
        else:
            if not room.both_connected():
                self.error(conn, "WAITING_PLAYER")
                return
            if kind == "extend":
                if room.match.phase != "finished":
                    self.error(conn, "INVALID_STATE")
                    return
                room.match.extend()
            else:
                room.match.start()
            room.reason = ""
            room.stop_inputs()
        room.activity = now
        self.broadcast_room(room)
        self.broadcast_state(room)

    def sweep(self, now):
        self.limits.sweep(now)
        for conn in list(self.connections):
            if not conn.seat and now-conn.joined_at > 10:
                self.close(conn, 1008, "AUTH_TIMEOUT")
        for room in list(self.rooms.values()):
            host, guest = room.seats
            if not host.conn and now-host.disconnected_at > self.grace_seconds:
                self.close_room(room, "HOST_LEFT")
            elif now-room.activity > self.idle_seconds or now-room.born > self.max_age_seconds:
                self.close_room(room, "ROOM_EXPIRED")
            elif guest and not guest.conn and now-guest.disconnected_at > self.grace_seconds:
                self.remove_guest(room)

    async def run(self):
        previous = last_cleanup = time.monotonic()
        next_tick = previous+1/60
        next_snapshot = previous+1/30
        peak_lag = 0
        try:
            while True:
                await asyncio.sleep(max(0, next_tick-time.monotonic()))
                now = time.monotonic()
                peak_lag = max(peak_lag, max(0, now-next_tick)*1000)
                next_tick += 1/60
                if next_tick < now:
                    next_tick = now+1/60
                elapsed, previous = min(100, (now-previous)*1000), now
                for room in self.rooms.values():
                    masks = [s.mask if s and s.conn and not s.conn.closing and now-s.last_input <= .3 else 0 for s in room.seats]
                    phase = room.match.phase
                    room.match.advance(elapsed, masks)
                    if room.match.phase != phase:
                        self.broadcast_room(room)
                if now >= next_snapshot:
                    next_snapshot += 1/30
                    if next_snapshot < now:
                        next_snapshot = now+1/30
                    for room in self.rooms.values():
                        if room.match.phase != "lobby":
                            self.broadcast_state(room)
                if now-last_cleanup >= 1:
                    last_cleanup = now
                    self.sweep(now)
                    self.server_lag_ms, peak_lag = peak_lag, 0
                    for room in self.rooms.values():
                        self.broadcast_network(room, now)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Volleyball simulation stopped")
            for conn in self.connections:
                self.close(conn, 1011, "SERVER_ERROR")
            raise

    async def serve(self, websocket, ip):
        now = time.monotonic()
        if (not self.running or len(self.connections) >= config.VOLLEYBALL_MAX_CONNECTIONS
                or sum(c.ip == ip for c in self.connections) >= config.VOLLEYBALL_MAX_CONNECTIONS_PER_IP
                or not self.upgrades.take(now) or not self.limits.take(("upgrade", ip), 15, 60, now)):
            await websocket.close(code=1013)
            return
        conn = Connection(websocket, ip)
        self.connections.add(conn)
        tasks = []
        try:
            await websocket.accept(subprotocol="volley-v1")
            self.send(conn, dict(type="hello", protocol=1))

            async def read():
                while not conn.closing:
                    packet = await websocket.receive()
                    if packet["type"] == "websocket.disconnect":
                        return
                    if not conn.messages.take():
                        self.close(conn, 1008, "RATE_LIMIT")
                        return
                    if packet.get("bytes") is not None:
                        self.close(conn, 1003, "TEXT_ONLY")
                        return
                    text = packet.get("text", "")
                    if len(text.encode("utf-8")) > 2048:
                        self.close(conn, 1009, "MESSAGE_TOO_BIG")
                        return
                    try:
                        # JSON booleans, NaN and unknown fields never become
                        # trusted settings or coordinates.
                        def invalid_constant(value):
                            raise ValueError(value)
                        msg = json.loads(text, parse_constant=invalid_constant)
                    except (ValueError, RecursionError):
                        self.protocol_error(conn)
                        continue
                    try:
                        self.handle(conn, msg)
                    except Exception:
                        logger.exception("Volleyball message failed")
                        self.close(conn, 1011, "SERVER_ERROR")
                        return

            async def write():
                while True:
                    packet = await conn.queue.get()
                    if packet is SNAPSHOT:
                        packet = conn.latest_state
                        conn.latest_state, conn.state_queued = None, False
                    if isinstance(packet, tuple):
                        await websocket.close(code=packet[0], reason=packet[1])
                        return
                    await asyncio.wait_for(websocket.send_text(packet), timeout=5)

            reader, writer = asyncio.create_task(read()), asyncio.create_task(write())
            tasks = [reader, writer]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            if reader in done and conn.closing and not writer.done():
                # Flush the error/closed packet and close code before cleanup.
                await asyncio.wait_for(asyncio.shield(writer), timeout=5)
        except (WebSocketDisconnect, OSError, RuntimeError, asyncio.TimeoutError):
            with suppress(RuntimeError, WebSocketDisconnect, OSError):
                await websocket.close(code=1013)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.disconnect(conn)


volleyball_server = VolleyballServer()


@asynccontextmanager
async def volleyball_lifespan():
    try:
        if config.VOLLEYBALL_ENABLED:
            await volleyball_server.start()
        yield
    finally:
        await volleyball_server.stop()
