import hmac
import hashlib
import time
import base64
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from config import config

router = APIRouter()

# TURN конфигурация
TURN_SECRET = config.TURN_SECRET.get_secret_value()
TURN_USERNAME = "turnuser"
TURN_SERVERS = [
    "turn:5.42.124.68:3478?transport=udp",
    "turn:5.42.124.68:3478?transport=tcp"
]

# Эндпоинт для получения временных учетных данных TURN
@router.get("/turn-credentials")
async def get_turn_credentials():
    """
    Генерирует временные учетные данные для TURN-сервера по схеме RFC 5766.
    Действительны 24 часа с момента генерации.
    """
    try:
        # Временная метка: текущее время + 86400 секунд (24 часа)
        timestamp = int(time.time()) + 86400
        username = f"{timestamp}:{TURN_USERNAME}"
        
        # Генерация HMAC-SHA1 хеша: secret + username
        hmac_hash = hmac.new(
            TURN_SECRET.encode("utf-8"),
            username.encode("utf-8"),
            hashlib.sha1
        ).digest()
        
        password = base64.b64encode(hmac_hash).decode("utf-8")
        
        return {
            "username": username,
            "credential": password,
            "urls": TURN_SERVERS
        }
    except Exception as e:
        print(f"[TURN] Ошибка генерации учетных данных: {e}")
        raise HTTPException(status_code=500, detail="Ошибка генерации учетных данных TURN")

@router.get("/")
async def redirect_to_chat():
    """Главная страница перенаправляет в чат без циклических редиректов"""
    return RedirectResponse(url="/chat", status_code=302)

@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("static/image/logo/favicon.ico")