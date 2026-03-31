"""Dental Practice Chatbot — FastAPI Backend."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.cache.redis_client import close_redis, get_redis, is_using_fallback
from src.config import get_settings
from src.db.database import init_db

settings = get_settings()

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup / shutdown lifecycle hook.

    Initializes all services eagerly so failures surface at startup
    (not on the first user request).
    """
    # --- Database ---
    logger.info("Initializing database...")
    init_db()
    logger.info("Database ready.")

    # --- Redis ---
    logger.info("Connecting to Redis...")
    try:
        await get_redis()
        if is_using_fallback():
            logger.warning("Redis unavailable — using in-memory fallback.")
        else:
            logger.info("Redis connected.")
    except Exception:
        logger.warning("Redis connection failed — using in-memory fallback.", exc_info=True)

    # --- ChromaDB ---
    logger.info("Initializing ChromaDB...")
    try:
        from src.vector.chroma_client import get_knowledge_collection, get_conversations_collection
        kb = get_knowledge_collection()
        conv = get_conversations_collection()
        logger.info(
            "ChromaDB ready (dental_kb: %d docs, conversations: %d docs).",
            kb.count(), conv.count(),
        )
    except Exception:
        logger.error("ChromaDB initialization failed.", exc_info=True)

    yield

    # --- Shutdown ---
    logger.info("Closing Redis connection pool...")
    await close_redis()
    logger.info("Shutting down.")


app = FastAPI(
    title="Dental Practice Chatbot API",
    description="Agentic AI assistant for patient intake, appointment booking, and dental knowledge Q&A",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "dental-chatbot-api"}
