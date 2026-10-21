"""Public game assets and the Python game's WebSocket endpoint."""

from fastapi import APIRouter, HTTPException, WebSocket
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from config import config
from security import normalize_allowed_origins, websocket_origin_allowed
from services.volleyball import GAME_ROOT, volleyball_server

router = APIRouter(prefix="/volleyball", tags=["volleyball"])
PUBLIC_FILES = {"index.html", "config.js", "online.js", "appearance.js", "online.css", "mobile.css", "shared/settings.js", "shared/physics.js", "shared/prediction.js", "shared/snapshots.js", "shared/net.js", "shared/balls.js"}
BACKGROUNDS = [
    {"id": "blue", "label": "Синие волейбольные каракули", "file": "Синие волейбольные каракули на полях-1.png"},
    {"id": "pencil", "label": "Карандашные зарисовки", "file": "Карандашные волейбольные зарисовки на полях-2.png"},
    {"id": "doodles", "label": "Тетрадный лист с дудлами", "file": "Тетрадный лист с игривыми дудлами-3.png"},
    {"id": "sea", "label": "Морские заметки", "file": "Морские заметки синей ручкой-4.png"},
    {"id": "beach", "label": "Пляж с пальмами", "file": "Графитный пляжный пейзаж с пальмами-5.png"},
    {"id": "lined", "label": "Линованный волейбольный лист", "file": "Линованный фон с волейбольными зарисовками-6.png"},
    {"id": "forest", "label": "Лесная поляна", "file": "Графитовая поляна среди деревьев-7.png"},
]
BACKGROUND_FILES = {"assets/"+item["file"] for item in BACKGROUNDS}
for background in BACKGROUNDS:
    background["thumbnail"] = "assets/thumbnails/"+background["id"]+".webp"
BACKGROUND_FILES.update(item["thumbnail"] for item in BACKGROUNDS)


@router.get("/healthz")
async def volleyball_health():
    if not config.VOLLEYBALL_ENABLED or not volleyball_server.running:
        raise HTTPException(status_code=503, detail="Game server unavailable")
    return {"ok": True, "protocol": 1}


@router.get("/online", include_in_schema=False)
async def volleyball_redirect():
    return RedirectResponse("/volleyball/online/", status_code=307)


@router.api_route("/online/", methods=["GET", "HEAD"], include_in_schema=False)
@router.api_route("/online/{filename:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def volleyball_asset(filename: str = "index.html"):
    filename = filename or "index.html"
    if not config.VOLLEYBALL_ENABLED:
        raise HTTPException(status_code=404)
    if filename == "backgrounds.json":
        return JSONResponse(BACKGROUNDS, headers={"Cache-Control": "no-cache"})
    if filename not in PUBLIC_FILES | BACKGROUND_FILES:
        raise HTTPException(status_code=404)
    return FileResponse(
        GAME_ROOT / "docs" / filename,
        headers={"Cache-Control": "no-store" if filename in {"index.html", "config.js"} else "no-cache"},
    )


@router.websocket("/ws")
async def volleyball_websocket(websocket: WebSocket):
    if not websocket_origin_allowed(websocket.headers.get("origin"), normalize_allowed_origins(config.ALLOWED_ORIGINS)):
        await websocket.close(code=1008)
        return
    if "volley-v1" not in websocket.scope.get("subprotocols", []):
        await websocket.close(code=1008)
        return
    if not config.VOLLEYBALL_ENABLED:
        await websocket.close(code=1013)
        return
    # Uvicorn handles trusted platform proxy addresses. Do not accept an
    # arbitrary client-supplied X-Forwarded-For as a rate-limit identity.
    ip = websocket.client.host if websocket.client else "unknown"
    await volleyball_server.serve(websocket, ip)
