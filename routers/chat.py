import asyncio
import uuid
import json
import time
from collections import defaultdict, deque
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from config import config
from routers.call_signaling import is_call_signal_type
from security import (
    normalize_allowed_origins,
    websocket_origin_allowed,
    get_websocket_client_ip,
    websocket_same_host_allowed,
)

router = APIRouter()
templates = Jinja2Templates(directory="templates")
allowed_origins = normalize_allowed_origins(config.ALLOWED_ORIGINS)

clients = {}
clients_by_session = {}
disconnect_cleanup_tasks = {}
rooms_chat = {}
connect_attempts = defaultdict(deque)
relay_usage = defaultdict(deque)
room_create_usage = defaultdict(deque)

WS_CONNECT_WINDOW_SECONDS = 60
WS_CONNECT_MAX_PER_IP = 40
RELAY_LIMIT_ENABLED = config.RELAY_LIMIT_ENABLED
RELAY_WINDOW_SECONDS = config.RELAY_WINDOW_SECONDS
RELAY_MAX_BYTES_PER_WINDOW = config.RELAY_MAX_BYTES_PER_WINDOW
ROOM_CREATE_WINDOW_SECONDS = 60
ROOM_CREATE_MAX_PER_WINDOW = 8
MOBILE_RECONNECT_GRACE_SECONDS = 180

async def safe_send_json(ws, message):
    try:
        await ws.send_json(message)
        return True
    except Exception:
        return False

def get_room_other_id(room: dict, client_id: str) -> str | None:
    return room.get("peer") if room.get("host") == client_id else room.get("host")


def build_peer_payload(room_id: str, client_id: str, other_id: str) -> dict:
    room = rooms_chat[room_id]
    return {
        "room_id": room_id,
        "peer_id": other_id,
        "peer_nickname": clients[other_id]["nickname"],
        "role": "host" if room.get("host") == client_id else "peer",
        "connection_id": room.get("connection_id"),
        "offerer_id": room.get("offerer_id") or room.get("host"),
        "reconnect_grace_seconds": MOBILE_RECONNECT_GRACE_SECONDS,
    }


def rotate_room_connection(room_id: str) -> None:
    if room_id in rooms_chat:
        room = rooms_chat[room_id]
        room["connection_id"] = str(uuid.uuid4())
        room["offerer_id"] = room.get("host")


async def mark_client_temporarily_disconnected(
    client_id: str, expected_ws: WebSocket | None = None
):
    if client_id not in clients:
        return
    client = clients[client_id]
    if expected_ws is not None and client.get("ws") is not expected_ws:
        return
    if not client.get("connected", True):
        return
    client["connected"] = False
    client["disconnected_at"] = time.time()
    cleanup_task = disconnect_cleanup_tasks.get(client_id)
    if cleanup_task:
        cleanup_task.cancel()
    disconnect_cleanup_tasks[client_id] = asyncio.create_task(
        finalize_client_disconnect(client_id, expected_ws)
    )
    await notify_peer_left(client_id, "peer_temporarily_disconnected")
    broadcast_users()


async def send_to_client(client_id: str, message: dict) -> bool:
    client = clients.get(client_id)
    if not client or not client.get("connected", True):
        return False
    ok = await safe_send_json(client["ws"], message)
    if not ok:
        await mark_client_temporarily_disconnected(client_id, client.get("ws"))
    return ok

def broadcast_users():
    waiting_list = []
    for cid, data in clients.items():
        if (
            data.get("status") == "waiting"
            and data.get("room_id") in rooms_chat
            and data.get("connected", True)
        ):
            room = rooms_chat[data["room_id"]]
            waiting_list.append(
                {"client_id": cid, "nickname": data["nickname"], "title": room["title"]}
            )
    for cid, client in clients.items():
        if client.get("connected", True):
            asyncio.create_task(
                send_to_client(cid, {"type": "users", "data": waiting_list})
            )

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
    if not RELAY_LIMIT_ENABLED:
        return True
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

async def notify_peer_left(client_id: str, message_type: str = "peer_left"):
    room_id = clients.get(client_id, {}).get("room_id")
    if not room_id or room_id not in rooms_chat:
        return
    room = rooms_chat[room_id]
    other = get_room_other_id(room, client_id)
    if other and other in clients and clients[other].get("connected", True):
        payload = {"reconnect_grace_seconds": MOBILE_RECONNECT_GRACE_SECONDS}
        if message_type in {"peer_temporarily_disconnected", "peer_disconnect_timeout"}:
            payload.update({"peer_id": client_id, "room_id": room_id})
        await send_to_client(other, {"type": message_type, "data": payload})


def clear_client_room(client_id: str):
    room_id = clients.get(client_id, {}).get("room_id")
    if not room_id or room_id not in rooms_chat:
        if client_id in clients:
            clients[client_id]["status"] = "online"
            clients[client_id]["room_id"] = None
        return

    room = rooms_chat[room_id]
    other = get_room_other_id(room, client_id)
    rooms_chat.pop(room_id, None)
    for participant_id in (client_id, other):
        if participant_id and participant_id in clients:
            clients[participant_id]["status"] = "online"
            clients[participant_id]["room_id"] = None

async def finalize_client_disconnect(
    client_id: str, expected_ws: WebSocket | None = None
):
    await asyncio.sleep(MOBILE_RECONNECT_GRACE_SECONDS)
    client = clients.get(client_id)
    if not client:
        disconnect_cleanup_tasks.pop(client_id, None)
        return
    if expected_ws is not None and client.get("ws") is not expected_ws:
        disconnect_cleanup_tasks.pop(client_id, None)
        return
    if client.get("connected", True):
        disconnect_cleanup_tasks.pop(client_id, None)
        return

    await notify_peer_left(client_id, "peer_disconnect_timeout")
    clear_client_room(client_id)
    session_key = client.get("session_key")
    if session_key:
        clients_by_session.pop(session_key, None)
    clients.pop(client_id, None)
    relay_usage.pop(client_id, None)
    room_create_usage.pop(client_id, None)
    disconnect_cleanup_tasks.pop(client_id, None)
    broadcast_users()

@router.websocket("/ws")
async def lobby_ws(websocket: WebSocket):
    client_ip = get_websocket_client_ip(
        websocket,
        trust_proxy_headers=config.TRUST_PROXY_HEADERS,
    )
    if not check_connect_rate_limit(client_ip):
        await websocket.close(code=4429, reason="Too many connection attempts")
        return
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host")
    if not (
        websocket_origin_allowed(origin, allowed_origins)
        or websocket_same_host_allowed(origin, host)
    ):
        await websocket.close(code=4403, reason="Origin not allowed")
        return
    await websocket.accept()
    session_key = (websocket.query_params.get("session") or "")[:80]
    tab_id = (websocket.query_params.get("tab") or "")[:80]
    resumed = False
    if session_key and session_key in clients_by_session:
        client_id = clients_by_session[session_key]
        if client_id in clients:
            cleanup_task = disconnect_cleanup_tasks.pop(client_id, None)
            if cleanup_task:
                cleanup_task.cancel()
            previous_tab_id = clients[client_id].get("tab_id")
            previous_ws = clients[client_id].get("ws")
            if (
                previous_ws is not websocket
                and clients[client_id].get("connected", True)
                and previous_tab_id
                and tab_id
                and previous_tab_id != tab_id
            ):
                await safe_send_json(previous_ws, {"type": "session_taken_over"})
                await safe_send_json(websocket, {"type": "session_resumed_in_new_tab"})
            clients[client_id]["ws"] = websocket
            clients[client_id]["tab_id"] = tab_id or previous_tab_id
            clients[client_id]["connected"] = True
            clients[client_id].pop("disconnected_at", None)
            resumed = True
        else:
            clients_by_session.pop(session_key, None)

    if not resumed:
        client_id = str(uuid.uuid4())
        clients[client_id] = {
            "ws": websocket,
            "nickname": "Аноним",
            "status": "online",
            "room_id": None,
            "session_key": session_key or None,
            "tab_id": tab_id or None,
            "connected": True,
        }
        if session_key:
            clients_by_session[session_key] = client_id

    await websocket.send_json(
        {"type": "init", "data": {"client_id": client_id, "resumed": resumed}}
    )
    if resumed:
        room_id = clients[client_id].get("room_id")
        if room_id and room_id in rooms_chat:
            room = rooms_chat[room_id]
            other_id = get_room_other_id(room, client_id)
            if (
                other_id
                and other_id in clients
                and clients[other_id].get("connected", True)
            ):
                rotate_room_connection(room_id)
                await send_to_client(
                    other_id,
                    {
                        "type": "peer_reconnected",
                        "data": build_peer_payload(room_id, other_id, client_id),
                    },
                )
                await send_to_client(
                    client_id,
                    {
                        "type": "peer_reconnected",
                        "data": build_peer_payload(room_id, client_id, other_id),
                    },
                )
    broadcast_users()
    print(
        f"[CHAT] {'Возврат' if resumed else 'Новый клиент'}: {short_id(client_id)} ip={client_ip}"
    )

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
                            "data": {
                                "reason": "Слишком много созданий комнат. Подождите 1 минуту."
                            },
                        },
                    )
                    continue
                title = (
                    payload.get(
                        "title", f"Чат от {clients[client_id]['nickname']}"
                    ).strip()
                    or "Без названия"
                )
                room_id = str(uuid.uuid4())
                rooms_chat[room_id] = {
                    "host": client_id,
                    "peer": None,
                    "title": title,
                    "connection_id": None,
                    "offerer_id": client_id,
                }
                clients[client_id]["room_id"] = room_id
                clients[client_id]["status"] = "waiting"
                await websocket.send_json(
                    {
                        "type": "room_created",
                        "data": {"room_id": room_id, "title": title},
                    }
                )
                broadcast_users()
                print(
                    f"[CHAT] Комната {room_id[:8]}... создана хозяином {client_id[:8]}..."
                )

            elif msg_type == "connect_request":
                target_id = payload.get("target_id")
                if (
                    target_id in clients
                    and clients[target_id]["status"] == "waiting"
                    and clients[target_id].get("connected", True)
                ):
                    await send_to_client(
                        target_id,
                        {
                            "type": "incoming_request",
                            "data": {
                                "from": client_id,
                                "from_nickname": clients[client_id]["nickname"],
                            },
                        },
                    )
                    print(
                        f"[CHAT] Запрос от {short_id(client_id)} к {short_id(target_id)}"
                    )
                else:
                    await safe_send_json(
                        websocket,
                        {
                            "type": "request_failed",
                            "data": {"reason": "Собеседник больше не доступен"},
                        },
                    )

            elif msg_type == "request_response":
                guest_id = payload.get("to")
                accepted = payload.get("accepted", False)
                host_id = client_id

                if not accepted:
                    if guest_id in clients:
                        await send_to_client(
                            guest_id,
                            {"type": "request_rejected", "data": {"by": host_id}},
                        )
                    continue

                if host_id not in clients or guest_id not in clients:
                    print(f"[CHAT] Ошибка: пользователи не найдены")
                    continue

                room_id = clients[host_id].get("room_id")
                if not room_id or room_id not in rooms_chat:
                    print(f"[CHAT] Ошибка: комната не найдена для {short_id(host_id)}")
                    await send_to_client(
                        guest_id,
                        {
                            "type": "request_failed",
                            "data": {"reason": "Комната уже не существует"},
                        },
                    )
                    continue

                room = rooms_chat[room_id]
                if room.get("peer") is not None:
                    print(f"[CHAT] Комната {short_id(room_id)} уже занята")
                    await send_to_client(
                        guest_id,
                        {
                            "type": "request_failed",
                            "data": {"reason": "Комната уже занята"},
                        },
                    )
                    continue

                room["peer"] = guest_id
                rotate_room_connection(room_id)
                clients[host_id]["status"] = "busy"
                clients[guest_id]["status"] = "busy"
                clients[guest_id]["room_id"] = room_id

                print(f"[CHAT] Соединение установлено в комнате {short_id(room_id)}")

                await send_to_client(
                    host_id,
                    {
                        "type": "start_connection",
                        "data": build_peer_payload(room_id, host_id, guest_id),
                    },
                )
                await send_to_client(
                    guest_id,
                    {
                        "type": "start_connection",
                        "data": build_peer_payload(room_id, guest_id, host_id),
                    },
                )
                broadcast_users()

            elif msg_type == "peer_disconnected":
                print(f"[CHAT] Клиент {short_id(client_id)} покидает чат")
                await notify_peer_left(client_id, "peer_left")
                clear_client_room(client_id)
                broadcast_users()

            elif msg_type in [
                "offer",
                "answer",
                "candidate",
                "public_key",
                "public_key_request",
                "relay_message",
                "transport_state",
            ] or is_call_signal_type(msg_type):
                target_id = payload.get("to")
                if target_id in clients and clients[target_id].get("connected", True):
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
                                    "data": {
                                        "reason": "Превышен лимит relay-трафика. Подождите несколько секунд."
                                    },
                                },
                            )
                            continue
                        relay_payload = payload.get("payload", {})
                        if not isinstance(relay_payload, dict):
                            await safe_send_json(
                                websocket,
                                {
                                    "type": "request_failed",
                                    "data": {"reason": "Некорректный relay payload"},
                                },
                            )
                            continue
                        if relay_payload.get("mode") != "encrypted_payload":
                            await safe_send_json(
                                websocket,
                                {
                                    "type": "request_failed",
                                    "data": {
                                        "reason": "Разрешён только зашифрованный relay payload"
                                    },
                                },
                            )
                            continue
                        if (
                            not relay_payload.get("encrypted")
                            or relay_payload.get("iv") is None
                        ):
                            await safe_send_json(
                                websocket,
                                {
                                    "type": "request_failed",
                                    "data": {
                                        "reason": "В relay payload отсутствуют данные шифрования"
                                    },
                                },
                            )
                            continue
                    payload["from"] = client_id
                    if msg_type == "call_request":
                        payload["from_nickname"] = clients[client_id]["nickname"]
                    if (
                        payload.get("connection_id")
                        and sender_room_id in rooms_chat
                        and payload.get("connection_id")
                        != rooms_chat[sender_room_id].get("connection_id")
                    ):
                        await safe_send_json(
                            websocket,
                            {
                                "type": "stale_signal",
                                "data": {"reason": "Устаревшее сигнальное сообщение"},
                            },
                        )
                        continue
                    payload.setdefault("room_id", sender_room_id)
                    payload.setdefault(
                        "connection_id", rooms_chat[sender_room_id].get("connection_id")
                    )
                    await send_to_client(target_id, data)
                else:
                    await safe_send_json(
                        websocket,
                        {
                            "type": "request_failed",
                            "data": {"reason": "Собеседник недоступен"},
                        },
                    )

    except WebSocketDisconnect:
        print(f"[CHAT] Клиент отключился: {short_id(client_id)}")
    except Exception as e:
        print(f"[CHAT] Ошибка: {e}")
    finally:
        await mark_client_temporarily_disconnected(client_id, websocket)

@router.get("/", response_class=HTMLResponse)
async def serve_chat(request: Request):
    return templates.TemplateResponse(request, "chat.html")


@router.get("/test", response_class=HTMLResponse)
async def test_turn(request: Request):
    return templates.TemplateResponse(request, "test.html")