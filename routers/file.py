import asyncio
import json
import uuid
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from config import config

router = APIRouter()
templates = Jinja2Templates(directory="templates")

rooms = {}

# TURN конфигурация (общая для всего приложения)
TURN_SECRET = config.TURN_SECRET.get_secret_value()
TURN_USERNAME = "turnuser"
TURN_SERVERS = [
    "turn:5.42.124.68:3478?transport=udp",
    "turn:5.42.124.68:3478?transport=tcp"
]

async def cleanup_rooms():
    while True:
        now = asyncio.get_event_loop().time()
        expired = [
            room_id for room_id, data in rooms.items()
            if data["expires"] < now and len(data["clients"]) == 0
        ]
        for room_id in expired:
            print(f"[FILE] Удалена пустая комната: {room_id}")
            del rooms[room_id]
        await asyncio.sleep(5)

@router.on_event("startup")
async def startup_event():
    asyncio.create_task(cleanup_rooms())

@router.get("/", response_class=HTMLResponse)
async def file_sender(request: Request):
    """Страница отправки файлов"""
    return templates.TemplateResponse("sender.html", {"request": request})

@router.get("/receiver", response_class=HTMLResponse)
async def file_receiver(request: Request):
    """Страница получения файлов (с параметрами room и #key)"""
    return templates.TemplateResponse("receiver.html", {"request": request})

@router.post("/create")
async def create_room(minutes: int):
    """Создание комнаты для передачи файла"""
    room_id = str(uuid.uuid4())
    rooms[room_id] = {
        "clients": [],
        "expires": asyncio.get_event_loop().time() + minutes * 60,
        "used": False,
    }
    print(f"[FILE] Создана комната {room_id} (действует {minutes} мин)")
    return {"room_id": room_id}

@router.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str):
    """WebSocket для передачи файлов"""
    await websocket.accept()
    
    if room_id not in rooms:
        await websocket.close(code=4000, reason="Комната не найдена")
        return

    room = rooms[room_id]
    room["clients"].append(websocket)

    # Уведомляем отправителя, когда получатель подключился
    if len(room["clients"]) == 2:
        sender_ws = room["clients"][0]
        try:
            await sender_ws.send_text(json.dumps({"type": "receiver_joined"}))
            print(f"[FILE] Получатель подключился к комнате {room_id}")
        except Exception as e:
            print(f"[FILE] Ошибка уведомления отправителя: {e}")

    try:
        while True:
            data = await websocket.receive()
            if "text" in data:
                message = json.loads(data["text"])
                if message.get("done"):
                    for c in room["clients"]:
                        await c.close()
                    rooms.pop(room_id, None)
                    break
                # Пересылаем всем остальным клиентам в комнате
                for c in room["clients"]:
                    if c != websocket:
                        await c.send_text(data["text"])
            elif "bytes" in data:
                for c in room["clients"]:
                    if c != websocket:
                        await c.send_bytes(data["bytes"])
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in room["clients"]:
            room["clients"].remove(websocket)
        # Уведомляем оставшегося участника
        if len(room["clients"]) == 1:
            remaining = room["clients"][0]
            try:
                await remaining.send_text(json.dumps({"type": "peer_disconnected"}))
                print(f"[FILE] Участник отключился из комнаты {room_id}")
            except:
                pass
        # Удаляем пустую комнату
        if not room["clients"] and room_id in rooms:
            print(f"[FILE] Комната {room_id} удалена (пустая)")
            del rooms[room_id]