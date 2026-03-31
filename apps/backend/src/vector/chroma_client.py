"""ChromaDB client — singleton PersistentClient with collection accessors.

Collections use ChromaDB's built-in all-MiniLM-L6-v2 embedding function
(384 dims, runs locally, zero API calls or credits).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection

from src.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton client
# ---------------------------------------------------------------------------

_client: ClientAPI | None = None
_lock = threading.Lock()


def get_chroma_client() -> ClientAPI:
    """Return (or create) the singleton ChromaDB PersistentClient.

    The persist directory is taken from ``Settings.CHROMA_PERSIST_DIR``.
    The directory is created automatically if it doesn't exist.
    """
    global _client
    if _client is not None:
        return _client

    with _lock:
        # Double-check after acquiring the lock.
        if _client is not None:
            return _client

        settings = get_settings()
        persist_dir = Path(settings.CHROMA_PERSIST_DIR)
        persist_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Initializing ChromaDB PersistentClient at %s", persist_dir)
        _client = chromadb.PersistentClient(path=str(persist_dir))
        return _client


# ---------------------------------------------------------------------------
# Collection helpers
# ---------------------------------------------------------------------------

_KNOWLEDGE_COLLECTION = "dental_kb"
_CONVERSATIONS_COLLECTION = "conversations"


def get_knowledge_collection() -> Collection:
    """Return the *dental_kb* collection (creates it on first call).

    Uses cosine distance and ChromaDB's default all-MiniLM-L6-v2 embeddings.
    """
    client = get_chroma_client()
    return client.get_or_create_collection(
        name=_KNOWLEDGE_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


def get_conversations_collection() -> Collection:
    """Return the *conversations* collection (creates it on first call).

    Uses cosine distance and ChromaDB's default all-MiniLM-L6-v2 embeddings.
    """
    client = get_chroma_client()
    return client.get_or_create_collection(
        name=_CONVERSATIONS_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


def reset_collections() -> None:
    """Delete and recreate both collections.  **Destructive** — use only in
    scripts or tests.
    """
    client = get_chroma_client()
    for name in (_KNOWLEDGE_COLLECTION, _CONVERSATIONS_COLLECTION):
        try:
            client.delete_collection(name)
            logger.info("Deleted collection '%s'", name)
        except Exception:
            pass  # Collection didn't exist yet.
    # Recreate so callers can use them immediately.
    get_knowledge_collection()
    get_conversations_collection()
    logger.info("Collections recreated.")
