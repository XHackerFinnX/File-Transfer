from .main import router as main_router
from .chat import router as chat_router
from .file import router as file_router

__all__ = ["main_router", "chat_router", "file_router"]