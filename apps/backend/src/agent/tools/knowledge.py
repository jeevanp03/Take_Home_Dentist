"""search_knowledge_base tool — RAG over dental_kb ChromaDB collection."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import numpy as np

from src.vector.chroma_client import get_knowledge_collection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MMR helpers
# ---------------------------------------------------------------------------

def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def _mmr_select(
    embeddings: list[list[float]],
    distances: list[float],
    *,
    k: int = 3,
    lambda_param: float = 0.5,
) -> list[int]:
    """Return indices of *k* candidates chosen by Maximal Marginal Relevance.

    ``distances`` are **cosine distances** (lower = more similar).
    We convert to a relevance score (1 - distance) so higher is better.
    """
    if len(embeddings) == 0:
        return []

    emb_array = np.array(embeddings)
    relevance = np.array([1.0 - d for d in distances])

    selected: list[int] = []
    candidates = list(range(len(embeddings)))

    for _ in range(min(k, len(embeddings))):
        best_idx: int | None = None
        best_score = -float("inf")

        for idx in candidates:
            rel = relevance[idx]

            # Max similarity to any already-selected document
            if selected:
                max_sim = max(
                    _cosine_similarity(emb_array[idx], emb_array[s])
                    for s in selected
                )
            else:
                max_sim = 0.0

            score = lambda_param * rel - (1 - lambda_param) * max_sim

            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx is None:
            break
        selected.append(best_idx)
        candidates.remove(best_idx)

    return selected


# ---------------------------------------------------------------------------
# Sync worker (runs in thread to avoid blocking event loop)
# ---------------------------------------------------------------------------

_DISTANCE_THRESHOLD = 0.5  # reject chunks with cosine distance > 0.5
_PRACTICE_BOOST = 0.1      # boost for practice-sourced documents


def _search_sync(query: str) -> dict[str, Any]:
    """Synchronous search — called via asyncio.to_thread()."""
    collection = get_knowledge_collection()

    if collection.count() == 0:
        logger.warning("Knowledge collection is empty — nothing to search.")
        return {"results": [], "message": "Knowledge base is empty."}

    raw = collection.query(
        query_texts=[query],
        n_results=5,
        include=["documents", "metadatas", "distances", "embeddings"],
    )

    documents: list[str] = raw["documents"][0] if raw["documents"] else []
    metadatas: list[dict] = raw["metadatas"][0] if raw["metadatas"] else []
    distances: list[float] = raw["distances"][0] if raw["distances"] else []
    embeddings: list[list[float]] = raw["embeddings"][0] if raw["embeddings"] else []

    if not documents:
        return {"results": [], "message": "No relevant information found."}

    # ---- MMR selection (top-5 → top-3 diverse) ----
    selected_indices = _mmr_select(
        embeddings, distances, k=3, lambda_param=0.5,
    )

    # ---- Build results with source-aware threshold + weighting ----
    results: list[dict[str, Any]] = []
    for idx in selected_indices:
        distance = distances[idx]
        meta = metadatas[idx] if idx < len(metadatas) else {}
        source_type = meta.get("source_type", "unknown")

        # Apply practice boost BEFORE threshold so practice docs
        # are more likely to survive the filter.
        effective_distance = distance
        if source_type == "practice":
            effective_distance = max(distance - _PRACTICE_BOOST, 0.0)

        if effective_distance > _DISTANCE_THRESHOLD:
            continue

        sim_score = 1.0 - effective_distance

        results.append(
            {
                "content": documents[idx],
                "source": meta.get("source", "unknown"),
                "similarity_score": round(sim_score, 4),
                "source_type": source_type,
            }
        )

    # Sort by similarity (highest first)
    results.sort(key=lambda r: r["similarity_score"], reverse=True)

    if not results:
        return {"results": [], "message": "No relevant information found."}

    logger.info(
        "search_knowledge_base returned %d results for query: %.80s",
        len(results),
        query,
    )
    return {"results": results}


# ---------------------------------------------------------------------------
# Async entry point
# ---------------------------------------------------------------------------

async def search_knowledge_base(query: str) -> dict[str, Any]:
    """Search the dental knowledge base with RAG + MMR reranking.

    Pipeline:
    1. Top-5 retrieval from ChromaDB (cosine distance).
    2. MMR to pick 3 diverse results.
    3. Source-aware threshold filter (practice docs get -0.1 distance boost).
    4. Sort by effective similarity.

    ChromaDB calls are synchronous — wrapped in ``asyncio.to_thread``
    to avoid blocking the FastAPI event loop.
    """
    try:
        return await asyncio.to_thread(_search_sync, query)
    except Exception:
        logger.exception("Error in search_knowledge_base")
        return {
            "results": [],
            "message": "An error occurred while searching the knowledge base.",
        }
