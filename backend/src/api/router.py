from __future__ import annotations

from fastapi import APIRouter

from src.api.chat import router as chat_router
from src.api.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(chat_router)
