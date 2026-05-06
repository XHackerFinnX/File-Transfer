from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, List, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

# Your project's config object. Keep the import path identical to room-auth-in.js
# so you don't have to touch anything else.
from config import config  # noqa: F401  (re-exported indirectly via TURN_SECRET below)

# --------------------------------------------------------------------------- #
# Constants & shared state                                                    #
# --------------------------------------------------------------------------- #

router = APIRouter()
ROOMS_LOCK = Lock()

# A peer is dropped from the "online" list this many seconds after the last
# heartbeat. Empty rooms are removed by the GC after ROOM_TTL_SECONDS.
STALE_TIMEOUT_SECONDS = 30
ROOM_TTL_SECONDS = 60 * 60 * 6   # 6h — kill abandoned rooms
GC_INTERVAL_SECONDS = 30

# Validation patterns: lets the client use Cyrillic nicknames but keeps room ids
# safe to embed in URLs.
ROOM_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{3,32}$")
NICK_RE = re.compile(r"^[\w\-\u0400-\u04FF\s]{1,64}$", re.UNICODE)

# In-memory storage. For production scale move this to Redis.
ROOMS: Dict[str, Dict[str, "PeerRecord"]] = {}
ROOM_PASSWORDS: Dict[str, str] = {}
ROOM_ENDPOINTS: Dict[str, dict] = {}
ROOM_TOUCHED_AT: Dict[str, float] = {}

# TURN -------------------------------------------------------------------- #
TURN_SECRET = config.TURN_SECRET.get_secret_value()
TURN_USERNAME = "turnuser"
TURN_SERVERS = [
    "turn:5.42.124.68:3478?transport=udp",
    "turn:5.42.124.68:3478?transport=tcp",
]

DEFAULT_CORS_ORIGINS = [
    "https://2p2p.ru",
    "http://localhost",
    "http://localhost:8000",
    "http://localhost:8080",
    # Eel default origins
    "http://localhost:8000",
    "null",
]


# --------------------------------------------------------------------------- #
# Pydantic models                                                             #
# --------------------------------------------------------------------------- #

class RoomAuthIn(BaseModel):
    room_name: str = Field(min_length=1, max_length=64)
    password: str = Field(default="", max_length=128)
    nickname: str = Field(min_length=1, max_length=64)
    user_id: str = Field(default="", max_length=64)

    @field_validator("room_name")
    @classmethod
    def _check_room_name(cls, v: str) -> str:
        v = v.strip()
        if not ROOM_ID_RE.match(v):
            raise ValueError(
                "room_name must be 3-32 chars: letters, digits, '_' or '-'"
            )
        return v

    @field_validator("nickname")
    @classmethod
    def _check_nick(cls, v: str) -> str:
        v = v.strip()
        if not v or not NICK_RE.match(v):
            raise ValueError("nickname must be 1-64 valid characters")
        return v


class PeerHeartbeatIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    nickname: str = Field(min_length=1, max_length=64)
    ping_ms: int = Field(default=0, ge=0, le=60_000)
    status: str = Field(default="Online", max_length=32)
    online: bool = True
    # ---- merged from server.py: LAN/public addressing -------------------- #
    public_ip: str = Field(default="", max_length=64)
    lan_ip: str = Field(default="", max_length=64)
    lan_port: int = Field(default=0, ge=0, le=65535)
    minecraft_port: int = Field(default=0, ge=0, le=65535)


class HostEndpointIn(BaseModel):
    host_ip: str = Field(min_length=1, max_length=64)
    host_port: int = Field(default=25565, ge=1, le=65535)


class AnnouncePortIn(BaseModel):
    """Host publishes its IP + LAN minecraft port to the room."""

    user_id: str = Field(min_length=1, max_length=64)
    public_ip: str = Field(default="", max_length=64)
    minecraft_port: int = Field(ge=1, le=65535)


class PeerRecord(BaseModel):
    user_id: str
    nickname: str
    ping_ms: int = 0
    status: str = "Online"
    online: bool = True
    last_seen: datetime
    public_ip: str = ""
    lan_ip: str = ""
    lan_port: int = 0
    minecraft_port: int = 0
    is_host: bool = False


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _touch(room: str) -> None:
    ROOM_TOUCHED_AT[room] = time.time()


def _client_ip(request: Request) -> str:
    """Best-effort extraction of the caller's IP, honoring X-Forwarded-For
    (your reverse proxy on 2p2p.ru sets it)."""
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if fwd:
        return fwd
    if request.client:
        return request.client.host
    return ""


# --------------------------------------------------------------------------- #
# Endpoints                                                                   #
# --------------------------------------------------------------------------- #

@router.post("/rooms/create")
def create_room(payload: RoomAuthIn, request: Request):
    room = payload.room_name
    with ROOMS_LOCK:
        if room not in ROOMS:
            ROOMS[room] = {}
            ROOM_PASSWORDS[room] = payload.password
        elif ROOM_PASSWORDS.get(room, "") != payload.password:
            raise HTTPException(status_code=403, detail="wrong room password")

        # First peer who creates the room is the host.
        if payload.user_id and payload.user_id not in ROOMS[room]:
            ROOMS[room][payload.user_id] = PeerRecord(
                user_id=payload.user_id,
                nickname=payload.nickname,
                last_seen=datetime.now(timezone.utc),
                public_ip=_client_ip(request),
                is_host=True,
            )
        _touch(room)

    return {"ok": True, "room_id": room, "is_host": True}


@router.post("/rooms/join")
def join_room(payload: RoomAuthIn, request: Request):
    room = payload.room_name
    with ROOMS_LOCK:
        if room not in ROOMS:
            raise HTTPException(status_code=404, detail="room not found")
        if ROOM_PASSWORDS.get(room, "") != payload.password:
            raise HTTPException(status_code=403, detail="wrong room password")

        if payload.user_id:
            existing = ROOMS[room].get(payload.user_id)
            ROOMS[room][payload.user_id] = PeerRecord(
                user_id=payload.user_id,
                nickname=payload.nickname,
                last_seen=datetime.now(timezone.utc),
                public_ip=_client_ip(request),
                is_host=existing.is_host if existing else False,
            )
        _touch(room)

    return {"ok": True, "room_id": room, "is_host": False}


@router.post("/rooms/{room}/heartbeat")
def upsert_room_peer(room: str, payload: PeerHeartbeatIn, request: Request):
    room = room.strip()
    if not ROOM_ID_RE.match(room):
        raise HTTPException(status_code=400, detail="invalid room id")

    with ROOMS_LOCK:
        if room not in ROOMS:
            ROOMS[room] = {}
            ROOM_PASSWORDS.setdefault(room, "")

        existing = ROOMS[room].get(payload.user_id)
        # The server is authoritative for the public IP we observe at TCP level —
        # fall back to whatever the client reported only if our view is empty.
        observed_ip = _client_ip(request) or payload.public_ip

        ROOMS[room][payload.user_id] = PeerRecord(
            user_id=payload.user_id,
            nickname=payload.nickname,
            ping_ms=payload.ping_ms,
            status=payload.status,
            online=payload.online,
            last_seen=datetime.now(timezone.utc),
            public_ip=observed_ip,
            lan_ip=payload.lan_ip,
            lan_port=payload.lan_port,
            minecraft_port=payload.minecraft_port or (existing.minecraft_port if existing else 0),
            is_host=existing.is_host if existing else False,
        )
        _touch(room)

    return {"ok": True, "room": room, "user_id": payload.user_id}


@router.post("/rooms/{room}/leave")
def leave_room(room: str, user_id: str):
    room = room.strip()
    with ROOMS_LOCK:
        peer = ROOMS.get(room, {}).get(user_id)
        if peer:
            peer.online = False
            peer.status = "Offline"
        _touch(room)
    return {"ok": True}


@router.get("/rooms/{room}/peers")
def get_room_peers(room: str):
    room = room.strip()
    if not ROOM_ID_RE.match(room):
        raise HTTPException(status_code=400, detail="invalid room id")

    now = datetime.now(timezone.utc)
    result: List[dict] = []
    with ROOMS_LOCK:
        peers = ROOMS.get(room, {})
        for peer in peers.values():
            age = (now - peer.last_seen).total_seconds()
            is_online = bool(peer.online and age <= STALE_TIMEOUT_SECONDS)
            result.append({
                "user_id": peer.user_id,
                "nickname": peer.nickname,
                "ping_ms": peer.ping_ms,
                "status": "Online" if is_online else "Offline",
                "online": is_online,
                "last_seen": peer.last_seen.isoformat(),
                "public_ip": peer.public_ip,
                "lan_ip": peer.lan_ip,
                "lan_port": peer.lan_port,
                "minecraft_port": peer.minecraft_port,
                "is_host": peer.is_host,
            })

    result.sort(key=lambda p: (not p["is_host"], not p["online"], p["nickname"].lower()))
    return {"room": room, "count": len(result), "peers": result}


@router.post("/rooms/{room}/host")
def set_room_host(room: str, payload: HostEndpointIn):
    """Legacy endpoint preserved for backwards compatibility."""
    room = room.strip()
    if not ROOM_ID_RE.match(room):
        raise HTTPException(status_code=400, detail="invalid room id")

    with ROOMS_LOCK:
        ROOM_ENDPOINTS[room] = {
            "host_ip": payload.host_ip,
            "host_port": payload.host_port,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _touch(room)
    return {"ok": True}


@router.get("/rooms/{room}/endpoint")
def get_room_endpoint(room: str):
    room = room.strip()
    if not ROOM_ID_RE.match(room):
        raise HTTPException(status_code=400, detail="invalid room id")
    endpoint = ROOM_ENDPOINTS.get(room)
    if not endpoint:
        # Fall back to whichever peer is host and has a minecraft_port.
        with ROOMS_LOCK:
            for peer in ROOMS.get(room, {}).values():
                if peer.is_host and peer.minecraft_port and peer.public_ip:
                    return {
                        "ok": True,
                        "endpoint": {
                            "host_ip": peer.public_ip,
                            "host_port": peer.minecraft_port,
                            "updated_at": peer.last_seen.isoformat(),
                            "from": "host_peer",
                        },
                    }
        raise HTTPException(status_code=404, detail="endpoint not set")
    return {"ok": True, "endpoint": endpoint}


@router.post("/rooms/{room}/announce-port")
def announce_port(room: str, payload: AnnouncePortIn, request: Request):
    """Host publishes its current Minecraft LAN port + public IP to the room.

    The launcher UI calls this when the user clicks "Опубликовать порт" in the
    Network Room. After that, every peer's GET /peers shows the host with a
    `minecraft_port` field, and they can copy `public_ip:minecraft_port` and
    paste it into Minecraft's "Connect to Server".
    """
    room = room.strip()
    if not ROOM_ID_RE.match(room):
        raise HTTPException(status_code=400, detail="invalid room id")

    observed_ip = _client_ip(request) or payload.public_ip
    if not observed_ip:
        raise HTTPException(status_code=400, detail="public_ip is empty")

    now = datetime.now(timezone.utc)
    with ROOMS_LOCK:
        if room not in ROOMS:
            raise HTTPException(status_code=404, detail="room not found")

        peer = ROOMS[room].get(payload.user_id)
        if peer is None:
            raise HTTPException(status_code=404, detail="peer not in room")

        peer.minecraft_port = payload.minecraft_port
        peer.public_ip = observed_ip
        peer.is_host = True
        peer.last_seen = now

        ROOM_ENDPOINTS[room] = {
            "host_ip": observed_ip,
            "host_port": payload.minecraft_port,
            "updated_at": now.isoformat(),
            "from": "announce_port",
        }
        _touch(room)

    return {
        "ok": True,
        "room_id": room,
        "host_ip": observed_ip,
        "host_port": payload.minecraft_port,
    }


# --------------------------------------------------------------------------- #
# TURN credentials                                                            #
# --------------------------------------------------------------------------- #

@router.get("/turn-credentials")
def get_turn_credentials():
    expires_at = int(time.time()) + 600
    username = f"{expires_at}:{TURN_USERNAME}"
    signature = hmac.new(
        TURN_SECRET.encode("utf-8"),
        username.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    credential = base64.b64encode(signature).decode("utf-8")
    return {
        "username": username,
        "credential": credential,
        "urls": TURN_SERVERS,
        "ttl": 600,
    }


# --------------------------------------------------------------------------- #
# Background GC of stale rooms                                                #
# --------------------------------------------------------------------------- #

async def _gc_loop() -> None:
    while True:
        try:
            now = time.time()
            now_dt = datetime.now(timezone.utc)
            with ROOMS_LOCK:
                # Drop offline peers older than STALE_TIMEOUT_SECONDS.
                for room_id, peers in list(ROOMS.items()):
                    for uid, peer in list(peers.items()):
                        age = (now_dt - peer.last_seen).total_seconds()
                        if age > STALE_TIMEOUT_SECONDS:
                            peer.online = False
                            peer.status = "Offline"
                        # Hard-evict pre-historic peers entirely so the list
                        # doesn't grow unbounded.
                        if age > ROOM_TTL_SECONDS:
                            peers.pop(uid, None)

                    if not peers and now - ROOM_TOUCHED_AT.get(room_id, now) > ROOM_TTL_SECONDS:
                        ROOMS.pop(room_id, None)
                        ROOM_PASSWORDS.pop(room_id, None)
                        ROOM_ENDPOINTS.pop(room_id, None)
                        ROOM_TOUCHED_AT.pop(room_id, None)
        except Exception:  # noqa: BLE001  — never let the GC kill the loop
            pass
        await asyncio.sleep(GC_INTERVAL_SECONDS)


@asynccontextmanager
async def room_lifespan(app: FastAPI):
    """Wire this into your FastAPI lifespan so GC starts/stops with the app."""
    task = asyncio.create_task(_gc_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass