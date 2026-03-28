import asyncio
import json
import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI()

@app.middleware("http")
async def https_redirect_middleware(request, call_next):
    # Для некоторых платформ помогает явно указать схему
    if request.headers.get("x-forwarded-proto") == "https":
        request.scope["scheme"] = "https"
    return await call_next(request)

# ================= CORS =================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

rooms = {}

# ================= CLEANUP =================
async def cleanup_rooms():
    while True:
        now = asyncio.get_event_loop().time()

        # Удаляем ТОЛЬКО пустые комнаты, которые просрочились
        expired = [
            room_id
            for room_id, data in rooms.items()
            if data["expires"] < now and len(data["clients"]) == 0
        ]

        for room_id in expired:
            print(f"[CLEANUP] Removing expired empty room: {room_id}")
            del rooms[room_id]

        await asyncio.sleep(5)


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(cleanup_rooms())


# ================= PAGES =================
@app.get("/")
async def index():
    return FileResponse("index.html")


@app.get("/receiver")
async def receiver():
    return FileResponse("receiver.html")


@app.get("/sender.js")
async def sender_js():
    return FileResponse("sender.js")


@app.get("/receiver.js")
async def receiver_js():
    return FileResponse("receiver.js")


# ================= CREATE ROOM =================
@app.post("/create")
async def create_room(minutes: int):
    room_id = str(uuid.uuid4())

    rooms[room_id] = {
        "clients": [],
        "expires": asyncio.get_event_loop().time() + minutes * 60,
        "used": False,
    }

    print(f"[CREATE] Room {room_id} (valid {minutes} min)")
    return {"room_id": room_id}


# ================= WEBSOCKET =================
@app.websocket("/ws/{room_id}")
async def websocket_endpoint(ws: WebSocket, room_id: str):
    await ws.accept()

    if room_id not in rooms:
        await ws.close()
        return

    room = rooms[room_id]
    room["clients"].append(ws)

    # === НОВОЕ: уведомляем sender'а, когда receiver подключился ===
    if len(room["clients"]) == 2:
        sender_ws = room["clients"][0]
        try:
            await sender_ws.send_text(json.dumps({"type": "receiver_joined"}))
            print(f"[SIGNAL] Notified sender → receiver joined room {room_id}")
        except Exception as e:
            print(f"[SIGNAL] Failed to notify sender: {e}")

    try:
        while True:
            try:
                data = await ws.receive()
            except:
                break

            # TEXT (control messages / signaling)
            if "text" in data:
                message = json.loads(data["text"])

                if message.get("done"):
                    for c in room["clients"]:
                        await c.close()
                    if room_id in rooms:
                        del rooms[room_id]
                    break

                # пересылаем всем остальным
                for c in room["clients"]:
                    if c != ws:
                        await c.send_text(data["text"])

            # BINARY (не используется в этой версии, но оставил)
            elif "bytes" in data:
                for c in room["clients"]:
                    if c != ws:
                        await c.send_bytes(data["bytes"])

    except WebSocketDisconnect:
        pass

    finally:
        if ws in room["clients"]:
            room["clients"].remove(ws)
            
        # === НОВОЕ: уведомляем оставшегося участника ===
        if len(room["clients"]) == 1:
            remaining = room["clients"][0]
            try:
                await remaining.send_text(json.dumps({"type": "peer_disconnected"}))
                print(f"[SIGNAL] Notified remaining peer → someone disconnected in room {room_id}")
            except:
                pass

        if not room["clients"] and room_id in rooms:
            print(f"[CLEANUP] Room {room_id} emptied, removing")
            del rooms[room_id]