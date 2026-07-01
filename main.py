import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from routers import (
    chat_router,
    launcher_feedback_router,
    launcher_router,
    main_router,
    tilda_apex_webhook_router,
)
from config import config
from db.connections import close_database_pools, open_database_pools
from db.schemas import create_tilda_submissions_schema
from security import normalize_allowed_origins, extract_origin_from_url

def ignore_windows_disconnect_noise(loop, context):
    exception = context.get("exception")
    handle = str(context.get("handle", ""))

    if isinstance(exception, ConnectionResetError) and "_call_connection_lost" in handle:
        return

    loop.default_exception_handler(context)
    
@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(ignore_windows_disconnect_noise)
    await asyncio.to_thread(open_database_pools, config.TILDA_APEX_DATABASE_TARGET)
    await create_tilda_submissions_schema(config.TILDA_APEX_DATABASE_TARGET)

    try:
        yield
    finally:
        await asyncio.to_thread(close_database_pools)

app = FastAPI(
    title="P2P Chat & File Transfer",
    lifespan=lifespan
)
allowed_origins = normalize_allowed_origins(config.ALLOWED_ORIGINS)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(allowed_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

@app.middleware("http")
async def https_redirect_middleware(request, call_next):
    if request.headers.get("x-forwarded-proto") == "https":
        request.scope["scheme"] = "https"
    response = await call_next(request)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(self), microphone=(self), speaker-selection=(self), "
        "display-capture=(self), geolocation=()"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self' ws: wss:; "
        "font-src 'self' data:; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )

    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    origin = request.headers.get("origin")
    if origin:
        normalized_origin = extract_origin_from_url(origin)
        if normalized_origin not in allowed_origins:
            response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    return response

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(chat_router, prefix="/chat")
app.include_router(main_router)
app.include_router(launcher_router, prefix="/launcher")
app.include_router(launcher_feedback_router)
app.include_router(tilda_apex_webhook_router)