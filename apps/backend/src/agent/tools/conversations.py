"""search_past_conversations tool — RAG over conversations ChromaDB collection."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.vector.chroma_client import get_conversations_collection

logger = logging.getLogger(__name__)

_DISTANCE_THRESHOLD = 0.7  # more lenient than knowledge (conversation summaries are noisier)


def _search_sync(patient_id: str, query: str) -> dict[str, Any]:
    """Synchronous search — called via asyncio.to_thread()."""
    collection = get_conversations_collection()

    if collection.count() == 0:
        logger.info("Conversations collection is empty.")
        return {"results": [], "message": "No past conversations found."}

    raw = collection.query(
        query_texts=[query],
        n_results=3,
        where={"patient_id": patient_id},
        include=["documents", "metadatas", "distances"],
    )

    documents: list[str] = raw["documents"][0] if raw["documents"] else []
    metadatas: list[dict] = raw["metadatas"][0] if raw["metadatas"] else []
    distances: list[float] = raw["distances"][0] if raw["distances"] else []

    if not documents:
        return {"results": [], "message": "No past conversations found."}

    results: list[dict[str, Any]] = []
    for doc, meta, dist in zip(documents, metadatas, distances):
        # Apply distance threshold — reject irrelevant results
        if dist > _DISTANCE_THRESHOLD:
            continue
        results.append(
            {
                "content": doc,
                "metadata": meta,
                "similarity_score": round(max(1.0 - dist, 0.0), 4),
            }
        )

    if not results:
        return {"results": [], "message": "No relevant past conversations found."}

    logger.info(
        "search_past_conversations returned %d results for patient %s",
        len(results),
        patient_id,
    )
    return {"results": results}


async def search_past_conversations(
    patient_id: str,
    query: str,
    *,
    session_id: str,
) -> dict[str, Any]:
    """Search past conversation history for a specific patient.

    ChromaDB calls are synchronous — wrapped in ``asyncio.to_thread``
    to avoid blocking the FastAPI event loop.
    """
    # Verify patient_id matches the session's identified patient
    from src.cache.session import get_session
    session = await get_session(session_id)
    session_patient = session.get("patient_id")
    if session_patient and session_patient != patient_id:
        return {"error": "Cannot search conversations for a different patient."}

    try:
        return await asyncio.to_thread(_search_sync, patient_id, query)
    except Exception:
        logger.exception("Error in search_past_conversations")
        return {
            "results": [],
            "message": "An error occurred while searching past conversations.",
        }
