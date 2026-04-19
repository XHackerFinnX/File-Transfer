import hmac
import hashlib
import time
import base64
from collections import defaultdict, deque
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, FileResponse
from config import config
from security import get_request_client_ip

router = APIRouter()

# TURN конфигурация
TURN_SECRET = config.TURN_SECRET.get_secret_value()
TURN_USERNAME = "turnuser"
TURN_SERVERS = [
    "turn:5.42.124.68:3478?transport=udp",
    "turn:5.42.124.68:3478?transport=tcp"
]
TURN_CREDENTIAL_TTL_SECONDS = 600
TURN_RATE_LIMIT_WINDOW = 60
TURN_RATE_LIMIT_MAX_REQUESTS = 30
turn_requests = defaultdict(deque)

# Эндпоинт для получения временных учетных данных TURN
@router.get("/turn-credentials")
async def get_turn_credentials(request: Request):
    """
    Генерирует временные учетные данные для TURN-сервера по схеме RFC 5766.
    Действительны 10 минут с момента генерации.
    """
    try:
        client_ip = get_request_client_ip(
            request,
            trust_proxy_headers=config.TRUST_PROXY_HEADERS,
        )
        now = int(time.time())
        bucket = turn_requests[client_ip]
        while bucket and bucket[0] <= now - TURN_RATE_LIMIT_WINDOW:
            bucket.popleft()
        if len(bucket) >= TURN_RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(status_code=429, detail="Слишком много запросов TURN")
        bucket.append(now)

        timestamp = now + TURN_CREDENTIAL_TTL_SECONDS
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
    except HTTPException:
        raise
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