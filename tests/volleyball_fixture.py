"""Test-only app: the real game routes/lifespan without platform databases."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from routers.volleyball import router
from services.volleyball import volleyball_lifespan


def create_app():
    @asynccontextmanager
    async def lifespan(app):
        async with volleyball_lifespan():
            yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    return app
