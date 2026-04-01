from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from routers import main_router, chat_router, file_router

app = FastAPI(title="P2P Chat & File Transfer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def https_redirect_middleware(request, call_next):
    if request.headers.get("x-forwarded-proto") == "https":
        request.scope["scheme"] = "https"
    return await call_next(request)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(chat_router, prefix="/chat")
app.include_router(file_router, prefix="/file")
app.include_router(main_router)