import asyncio
import uuid
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")

clients = {}
rooms_chat = {}

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

@router.websocket("/ws")
async def lobby_ws(websocket: WebSocket):
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
    print(f"[CHAT] Новый клиент: {client_id[:8]}...")

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            payload = data.get("data", {})

            if msg_type == "set_nickname":
                clients[client_id]["nickname"] = payload.get("nickname", "Аноним")[:20]
                broadcast_users()

            elif msg_type == "create_room":
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
                    print(f"[CHAT] Запрос от {client_id[:8]}... к {target_id[:8]}...")
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
                    print(f"[CHAT] Ошибка: комната не найдена для {host_id[:8]}...")
                    await safe_send_json(clients[guest_id]["ws"], {
                        "type": "request_failed",
                        "data": {"reason": "Комната уже не существует"}
                    })
                    continue

                room = rooms_chat[room_id]
                if room.get("peer") is not None:
                    print(f"[CHAT] Комната {room_id[:8]}... уже занята")
                    await safe_send_json(clients[guest_id]["ws"], {
                        "type": "request_failed",
                        "data": {"reason": "Комната уже занята"}
                    })
                    continue

                room["peer"] = guest_id
                clients[host_id]["status"] = "busy"
                clients[guest_id]["status"] = "busy"
                clients[guest_id]["room_id"] = room_id

                print(f"[CHAT] Соединение установлено в комнате {room_id[:8]}...")

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
                print(f"[CHAT] Клиент {client_id[:8]}... покидает чат")
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
                    payload["from"] = client_id
                    await safe_send_json(clients[target_id]["ws"], data)
                else:
                    await safe_send_json(websocket, {
                        "type": "request_failed",
                        "data": {"reason": "Собеседник недоступен"}
                    })

    except WebSocketDisconnect:
        print(f"[CHAT] Клиент отключился: {client_id[:8]}...")
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
            broadcast_users()

@router.get("/", response_class=HTMLResponse)
async def serve_chat(request: Request):
    return templates.TemplateResponse(request, "chat.html")


@router.get("/test", response_class=HTMLResponse)
async def test_turn(request: Request):
    return templates.TemplateResponse(request, "test.html")