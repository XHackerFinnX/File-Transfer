import base64
import hashlib
import hmac
import time
from urllib.parse import urlparse


def sign_capability_token(secret: str, room_id: str, ttl_seconds: int = 600) -> str:
    expires_at = int(time.time()) + ttl_seconds
    payload = f"{room_id}:{expires_at}"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    signature_b64 = base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")
    return f"{payload}:{signature_b64}"


def verify_capability_token(secret: str, room_id: str, token: str) -> bool:
    try:
        token_room_id, expires_raw, signature = token.split(":", 2)
        if token_room_id != room_id:
            return False
        expires_at = int(expires_raw)
        if expires_at < int(time.time()):
            return False

        payload = f"{token_room_id}:{expires_at}"
        expected = hmac.new(
            secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        expected_b64 = base64.urlsafe_b64encode(expected).decode("utf-8").rstrip("=")
        return hmac.compare_digest(signature, expected_b64)
    except Exception:
        return False


def normalize_allowed_origins(origins: list[str]) -> set[str]:
    normalized = set()
    for origin in origins:
        if not origin:
            continue
        normalized.add(origin.rstrip("/"))
    return normalized


def websocket_origin_allowed(origin: str | None, allowed_origins: set[str]) -> bool:
    if not origin:
        return False
    return origin.rstrip("/") in allowed_origins


def request_origin_allowed(request_origin: str | None, allowed_origins: set[str]) -> bool:
    if not request_origin:
        return False
    return request_origin.rstrip("/") in allowed_origins


def extract_origin_from_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"