import asyncio
import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

chat_app = FastAPI()

chat_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

clients = {}
rooms_chat = {}

async def safe_send_json(ws, message):
    """Безопасная отправка JSON через WebSocket с подавлением ошибок закрытого соединения."""
    try:
        await ws.send_json(message)
    except:
        pass

def broadcast_users():
    """Отправляет всем клиентам актуальный список ожидающих комнат."""
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

@chat_app.websocket("/ws")
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
    print(f"[SERVER] Новый клиент подключился: {client_id[:8]}...")

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
                
                await websocket.send_json({
                    "type": "room_created", 
                    "data": {"room_id": room_id, "title": title}
                })
                broadcast_users()
                print(f"[SERVER] Комната создана: {room_id[:8]}... хозяин {client_id[:8]}...")

            elif msg_type == "connect_request":
                target_id = payload.get("target_id")
                if target_id in clients and clients[target_id]["status"] == "waiting":
                    await clients[target_id]["ws"].send_json({
                        "type": "incoming_request",
                        "data": {"from": client_id, "from_nickname": clients[client_id]["nickname"]}
                    })
                    print(f"[SERVER] Запрос на подключение от {client_id[:8]}... к {target_id[:8]}...")
                else:
                    await safe_send_json(websocket, {
                        "type": "request_failed",
                        "data": {"reason": "Собеседник больше не доступен"}
                    })

            elif msg_type == "request_response":
                guest_id = payload.get("to")      # гость, который запрашивал подключение
                accepted = payload.get("accepted", False)
                host_id = client_id               # текущий клиент (хозяин комнаты)

                if not accepted:
                    if guest_id in clients:
                        await safe_send_json(clients[guest_id]["ws"], {
                            "type": "request_rejected",
                            "data": {"by": host_id}
                        })
                    continue

                # --- Принятие запроса ---
                if host_id not in clients or guest_id not in clients:
                    print(f"[SERVER] Ошибка: хозяин или гость не найдены")
                    continue

                room_id = clients[host_id].get("room_id")
                if not room_id or room_id not in rooms_chat:
                    print(f"[SERVER] Ошибка: комната не найдена для хозяина {host_id[:8]}...")
                    await safe_send_json(clients[guest_id]["ws"], {
                        "type": "request_failed",
                        "data": {"reason": "Комната уже не существует"}
                    })
                    continue

                room = rooms_chat[room_id]
                if room.get("peer") is not None:
                    print(f"[SERVER] Комната {room_id[:8]}... уже занята")
                    await safe_send_json(clients[guest_id]["ws"], {
                        "type": "request_failed",
                        "data": {"reason": "Комната уже занята"}
                    })
                    continue

                # Обновляем состояния
                room["peer"] = guest_id
                clients[host_id]["status"] = "busy"
                clients[guest_id]["status"] = "busy"
                clients[guest_id]["room_id"] = room_id

                print(f"[SERVER] Подтверждение OK. Начинаем P2P в комнате {room_id[:8]}...")

                # Отправляем start_connection обоим
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
                # Клиент сознательно покидает чат
                print(f"[SERVER] Клиент {client_id[:8]}... покидает чат")
                room_id = clients[client_id].get("room_id")
                if room_id and room_id in rooms_chat:
                    room = rooms_chat[room_id]
                    other = room["peer"] if room["host"] == client_id else room["host"]
                    if other and other in clients:
                        await safe_send_json(clients[other]["ws"], {
                            "type": "peer_disconnected"
                        })
                    # Удаляем комнату, если её создатель вышел
                    if room["host"] == client_id or not room.get("peer"):
                        rooms_chat.pop(room_id, None)
                # Сбрасываем статус клиента
                clients[client_id]["status"] = "online"
                clients[client_id]["room_id"] = None
                broadcast_users()

            elif msg_type in ["offer", "answer", "candidate", "public_key"]:
                target_id = payload.get("to")
                if target_id in clients:
                    payload["from"] = client_id
                    await safe_send_json(clients[target_id]["ws"], data)

    except WebSocketDisconnect:
        print(f"[SERVER] Клиент отключился: {client_id[:8]}...")
    except Exception as e:
        print(f"[SERVER] Ошибка в WebSocket: {e}")
    finally:
        # Очистка при разрыве соединения
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

@chat_app.get("/")
async def serve_chat():
    return FileResponse("chat.html")

@chat_app.get("/chat.js")
async def serve_js():
    return FileResponse("chat.js")