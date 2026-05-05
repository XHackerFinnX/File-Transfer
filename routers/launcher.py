"""FastAPI example for Launcher room API.

Supports:
- POST /launcher/rooms/create
- POST /launcher/rooms/join
- POST /launcher/rooms/{room}/heartbeat
- POST /launcher/rooms/{room}/leave
- GET  /launcher/rooms/{room}/peers
"""

from __future__ import annotations

from datetime import datetime, timezone
import base64
import hashlib
import hmac
import time
from threading import Lock
from typing import Dict, List

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field
from config import config

router = APIRouter()
ROOMS_LOCK = Lock()
STALE_TIMEOUT_SECONDS = 30
ROOM_PASSWORDS: Dict[str, str] = {}
ROOMS: Dict[str, Dict[str, "PeerRecord"]] = {}
ROOM_ENDPOINTS: Dict[str, dict] = {}
TURN_SECRET = config.TURN_SECRET.get_secret_value()
TURN_USERNAME = "turnuser"
TURN_SERVERS = [
    "turn:5.42.124.68:3478?transport=udp",
    "turn:5.42.124.68:3478?transport=tcp"
]


class RoomAuthIn(BaseModel):
    room_name: str = Field(min_length=1, max_length=64)
    password: str = Field(default="", max_length=128)
    nickname: str = Field(min_length=1, max_length=64)
    user_id: str = Field(default="", max_length=64)


class PeerHeartbeatIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    nickname: str = Field(min_length=1, max_length=64)
    ping_ms: int = Field(default=0, ge=0, le=60_000)
    status: str = Field(default="Online", max_length=32)
    online: bool = True


class PeerRecord(BaseModel):
    user_id: str
    nickname: str
    ping_ms: int
    status: str
    online: bool
    last_seen: datetime


@router.post("/rooms/create")
def create_room(payload: RoomAuthIn):
    room = payload.room_name.strip()
    if not room:
        raise HTTPException(status_code=400, detail="room_name is empty")

    with ROOMS_LOCK:
        if room not in ROOMS:
            ROOMS[room] = {}
            ROOM_PASSWORDS[room] = payload.password
        elif ROOM_PASSWORDS.get(room, "") != payload.password:
            raise HTTPException(status_code=403, detail="wrong room password")

    return {"ok": True, "room_id": room}


@router.post("/rooms/join")
def join_room(payload: RoomAuthIn):
    room = payload.room_name.strip()
    if not room:
        raise HTTPException(status_code=400, detail="room_name is empty")

    with ROOMS_LOCK:
        if room not in ROOMS:
            raise HTTPException(status_code=404, detail="room not found")
        if ROOM_PASSWORDS.get(room, "") != payload.password:
            raise HTTPException(status_code=403, detail="wrong room password")

    return {"ok": True, "room_id": room}


@router.post("/rooms/{room}/heartbeat")
def upsert_room_peer(room: str, payload: PeerHeartbeatIn):
    room = room.strip()
    if not room:
        raise HTTPException(status_code=400, detail="room is empty")
    with ROOMS_LOCK:
        if room not in ROOMS:
            ROOMS[room] = {}
            ROOM_PASSWORDS.setdefault(room, "")
        ROOMS[room][payload.user_id] = PeerRecord(
            user_id=payload.user_id,
            nickname=payload.nickname,
            ping_ms=payload.ping_ms,
            status=payload.status,
            online=payload.online,
            last_seen=datetime.now(timezone.utc),
        )
    return {"ok": True, "room": room, "user_id": payload.user_id}


@router.post("/rooms/{room}/leave")
def leave_room(room: str, user_id: str):
    room = room.strip()
    with ROOMS_LOCK:
        peer = ROOMS.get(room, {}).get(user_id)
        if peer:
            peer.online = False
            peer.status = "Offline"
    return {"ok": True}


@router.get("/rooms/{room}/peers")
def get_room_peers(room: str):
    room = room.strip()
    if not room:
        raise HTTPException(status_code=400, detail="room is empty")

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
            })

    result.sort(key=lambda p: (not p["online"], p["nickname"].lower()))
    return {"room": room, "count": len(result), "peers": result}


@router.post("/rooms/{room}/host")
def set_room_host(room: str, payload: dict):
    room = room.strip()
    if not room:
        raise HTTPException(status_code=400, detail="room is empty")
    host_ip = str(payload.get("host_ip") or "").strip()
    host_port = int(payload.get("host_port") or 25565)
    if not host_ip:
        raise HTTPException(status_code=400, detail="host_ip is empty")
    with ROOMS_LOCK:
        ROOM_ENDPOINTS[room] = {
            "host_ip": host_ip,
            "host_port": host_port,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    return {"ok": True}


@router.get("/rooms/{room}/endpoint")
def get_room_endpoint(room: str):
    room = room.strip()
    if not room:
        raise HTTPException(status_code=400, detail="room is empty")
    endpoint = ROOM_ENDPOINTS.get(room)
    if not endpoint:
        raise HTTPException(status_code=404, detail="endpoint not set")
    return {"ok": True, "endpoint": endpoint}


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
    return {"username": username, "credential": credential, "urls": TURN_SERVERS, "ttl": 600}