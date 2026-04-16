import asyncio
import uuid
import json
import time
from collections import defaultdict, deque
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from config import config
from security import normalize_allowed_origins, websocket_origin_allowed

router = APIRouter()
templates = Jinja2Templates(directory="templates")
allowed_origins = normalize_allowed_origins(config.ALLOWED_ORIGINS)

clients = {}
rooms_chat = {}
connect_attempts = defaultdict(deque)
relay_usage = defaultdict(deque)
room_create_usage = defaultdict(deque)

WS_CONNECT_WINDOW_SECONDS = 60
WS_CONNECT_MAX_PER_IP = 40
RELAY_WINDOW_SECONDS = 10
RELAY_MAX_BYTES_PER_WINDOW = 2 * 1024 * 1024
ROOM_CREATE_WINDOW_SECONDS = 60
ROOM_CREATE_MAX_PER_WINDOW = 8

async def safe_send_json(ws, message):
    try:
        await ws.send_json(message)
    except:
        pass

def broadcast_users():
    waiting_list = []
    for cid, data in clients.items():
        if data.get("status") == "waiting" and data.get("room_id") in rooms_chat:
            room = rooms_chat[data["room_id"]]
            waiting_list.append({
                "client_id": cid,
                "nickname": data["nickname"],
                "title": room["title"]
            })
    for client in clients.values():
        asyncio.create_task(safe_send_json(client["ws"], {"type": "users", "data": waiting_list}))

def short_id(value: str | None) -> str:
    if not value:
        return "unknown"
    return f"{value[:8]}..."

def check_connect_rate_limit(client_ip: str) -> bool:
    now = time.time()
    bucket = connect_attempts[client_ip]
    while bucket and bucket[0] <= now - WS_CONNECT_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= WS_CONNECT_MAX_PER_IP:
        return False
    bucket.append(now)
    return True

def consume_relay_budget(client_id: str, payload_size: int) -> bool:
    now = time.time()
    bucket = relay_usage[client_id]
    while bucket and bucket[0][0] <= now - RELAY_WINDOW_SECONDS:
        bucket.popleft()
    used = sum(size for _, size in bucket)
    if used + payload_size > RELAY_MAX_BYTES_PER_WINDOW:
        return False
    bucket.append((now, payload_size))
    return True

def consume_room_create_budget(client_id: str) -> bool:
    now = time.time()
    bucket = room_create_usage[client_id]
    while bucket and bucket[0] <= now - ROOM_CREATE_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= ROOM_CREATE_MAX_PER_WINDOW:
        return False
    bucket.append(now)
    return True

@router.websocket("/ws")
async def lobby_ws(websocket: WebSocket):
    client_ip = websocket.client.host if websocket.client else "unknown"
    if not check_connect_rate_limit(client_ip):
        await websocket.close(code=4429, reason="Too many connection attempts")
        return
    if not websocket_origin_allowed(websocket.headers.get("origin"), allowed_origins):
        await websocket.close(code=4403, reason="Origin not allowed")
        return
    await websocket.accept()
    client_id = str(uuid.uuid4())
    clients[client_id] = {
        "ws": websocket,
        "nickname": "Аноним",
        "status": "online",
        "room_id": None
    }
    await websocket.send_json({"type": "init", "data": {"client_id": client_id}})
    broadcast_users()
    print(f"[CHAT] Новый клиент: {short_id(client_id)} ip={client_ip}")

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            payload = data.get("data", {})

            if msg_type == "set_nickname":
                clients[client_id]["nickname"] = payload.get("nickname", "Аноним")[:20]
                broadcast_users()

            elif msg_type == "create_room":
                if not consume_room_create_budget(client_id):
                    await safe_send_json(
                        websocket,
                        {
                            "type": "request_failed",
                            "data": {"reason": "Слишком много созданий комнат. Подождите 1 минуту."},
                        },
                    )
                    continue
                title = payload.get("title", f"Чат от {clients[client_id]['nickname']}").strip() or "Без названия"
                room_id = str(uuid.uuid4())
                rooms_chat[room_id] = {"host": client_id, "peer": None, "title": title}
                clients[client_id]["room_id"] = room_id
                clients[client_id]["status"] = "waiting"
                await websocket.send_json({"type": "room_created", "data": {"room_id": room_id, "title": title}})
                broadcast_users()
                print(f"[CHAT] Комната {room_id[:8]}... создана хозяином {client_id[:8]}...")

            elif msg_type == "connect_request":
                target_id = payload.get("target_id")
                if target_id in clients and clients[target_id]["status"] == "waiting":
                    await clients[target_id]["ws"].send_json({
                        "type": "incoming_request",
                        "data": {"from": client_id, "from_nickname": clients[client_id]["nickname"]}
                    })
                    print(f"[CHAT] Запрос от {short_id(client_id)} к {short_id(target_id)}")
                else:
                    await safe_send_json(websocket, {
                        "type": "request_failed",
                        "data": {"reason": "Собеседник больше не доступен"}
                    })

            elif msg_type == "request_response":
                guest_id = payload.get("to")
                accepted = payload.get("accepted", False)
                host_id = client_id

                if not accepted:
                    if guest_id in clients:
                        await safe_send_json(clients[guest_id]["ws"], {
                            "type": "request_rejected",
                            "data": {"by": host_id}
                        })
                    continue

                if host_id not in clients or guest_id not in clients:
                    print(f"[CHAT] Ошибка: пользователи не найдены")
                    continue

                room_id = clients[host_id].get("room_id")
                if not room_id or room_id not in rooms_chat:
                    print(f"[CHAT] Ошибка: комната не найдена для {short_id(host_id)}")
                    await safe_send_json(clients[guest_id]["ws"], {
                        "type": "request_failed",
                        "data": {"reason": "Комната уже не существует"}
                    })
                    continue

                room = rooms_chat[room_id]
                if room.get("peer") is not None:
                    print(f"[CHAT] Комната {short_id(room_id)} уже занята")
                    await safe_send_json(clients[guest_id]["ws"], {
                        "type": "request_failed",
                        "data": {"reason": "Комната уже занята"}
                    })
                    continue

                room["peer"] = guest_id
                clients[host_id]["status"] = "busy"
                clients[guest_id]["status"] = "busy"
                clients[guest_id]["room_id"] = room_id

                print(f"[CHAT] Соединение установлено в комнате {short_id(room_id)}")

                await safe_send_json(clients[host_id]["ws"], {
                    "type": "start_connection",
                    "data": {
                        "room_id": room_id,
                        "peer_id": guest_id,
                        "peer_nickname": clients[guest_id]["nickname"],
                        "role": "host"
                    }
                })
                await safe_send_json(clients[guest_id]["ws"], {
                    "type": "start_connection",
                    "data": {
                        "room_id": room_id,
                        "peer_id": host_id,
                        "peer_nickname": clients[host_id]["nickname"],
                        "role": "peer"
                    }
                })
                broadcast_users()

            elif msg_type == "peer_disconnected":
                print(f"[CHAT] Клиент {short_id(client_id)} покидает чат")
                room_id = clients[client_id].get("room_id")
                if room_id and room_id in rooms_chat:
                    room = rooms_chat[room_id]
                    other = room["peer"] if room["host"] == client_id else room["host"]
                    if other and other in clients:
                        await safe_send_json(clients[other]["ws"], {"type": "peer_disconnected"})
                    if room["host"] == client_id or not room.get("peer"):
                        rooms_chat.pop(room_id, None)
                clients[client_id]["status"] = "online"
                clients[client_id]["room_id"] = None
                broadcast_users()

            elif msg_type in ["offer", "answer", "candidate", "public_key", "public_key_request", "relay_message", "transport_state"]:
                target_id = payload.get("to")
                if target_id in clients:
                    sender_room_id = clients[client_id].get("room_id")
                    target_room_id = clients[target_id].get("room_id")
                    if (
                        not sender_room_id
                        or sender_room_id != target_room_id
                        or sender_room_id not in rooms_chat
                    ):
                        await safe_send_json(
                            websocket,
                            {
                                "type": "request_failed",
                                "data": {
                                    "reason": "Невозможно отправить сообщение: нет активной общей комнаты"
                                },
                            },
                        )
                        continue
                    if msg_type == "relay_message":
                        relay_size = len(json.dumps(payload, ensure_ascii=False))
                        if not consume_relay_budget(client_id, relay_size):
                            await safe_send_json(
                                websocket,
                                {
                                    "type": "request_failed",
                                    "data": {"reason": "Превышен лимит relay-трафика. Подождите несколько секунд."},
                                },
                            )
                            continue
                        relay_payload = payload.get("payload", {})
                        if not isinstance(relay_payload, dict):
                            await safe_send_json(websocket, {"type": "request_failed", "data": {"reason": "Некорректный relay payload"}})
                            continue
                        if relay_payload.get("mode") != "encrypted_payload":
                            await safe_send_json(websocket, {"type": "request_failed", "data": {"reason": "Разрешён только зашифрованный relay payload"}})
                            continue
                        if not relay_payload.get("encrypted") or relay_payload.get("iv") is None:
                            await safe_send_json(websocket, {"type": "request_failed", "data": {"reason": "В relay payload отсутствуют данные шифрования"}})
                            continue
                    payload["from"] = client_id
                    await safe_send_json(clients[target_id]["ws"], data)
                else:
                    await safe_send_json(websocket, {
                        "type": "request_failed",
                        "data": {"reason": "Собеседник недоступен"}
                    })

    except WebSocketDisconnect:
        print(f"[CHAT] Клиент отключился: {short_id(client_id)}")
    except Exception as e:
        print(f"[CHAT] Ошибка: {e}")
    finally:
        if client_id in clients:
            room_id = clients[client_id].get("room_id")
            if room_id and room_id in rooms_chat:
                room = rooms_chat[room_id]
                other = room.get("peer") if room.get("host") == client_id else room.get("host")
                if other and other in clients:
                    await safe_send_json(clients[other]["ws"], {"type": "peer_disconnected"})
                if room.get("host") == client_id or not room.get("peer"):
                    rooms_chat.pop(room_id, None)
            clients.pop(client_id, None)
            relay_usage.pop(client_id, None)
            room_create_usage.pop(client_id, None)
            broadcast_users()

@router.get("/", response_class=HTMLResponse)
async def serve_chat(request: Request):
    return templates.TemplateResponse(request, "chat.html")


@router.get("/test", response_class=HTMLResponse)
async def test_turn(request: Request):
    return templates.TemplateResponse(request, "test.html")