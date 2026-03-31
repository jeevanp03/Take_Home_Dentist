"""Gemini 2.0 Flash LLM client with concurrency control and retry logic.

Uses the ``google-genai`` SDK (client-based API).
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import AsyncIterator

from google import genai
from google.genai import types
from google.api_core import exceptions as google_exceptions

from src.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state (lazy init with lock)
# ---------------------------------------------------------------------------
_client: genai.Client | None = None
_semaphore: asyncio.Semaphore | None = None
_init_lock: asyncio.Lock | None = None

# Retry config
_MAX_RETRIES = 2
_BACKOFF_BASE = 1.0  # seconds

# Per-call timeout (prevents hung API calls from holding semaphore forever)
_CALL_TIMEOUT = 30.0  # seconds

MODEL_NAME = "gemini-2.0-flash"


def _get_init_lock() -> asyncio.Lock:
    """Return the init lock, creating it if needed.

    asyncio.Lock must be created inside a running event loop, so we
    lazily create it on first use.
    """
    global _init_lock  # noqa: PLW0603
    if _init_lock is None:
        _init_lock = asyncio.Lock()
    return _init_lock


async def _get_client() -> genai.Client:
    """Return (or create) the singleton Gemini client (async-safe)."""
    global _client  # noqa: PLW0603
    if _client is not None:
        return _client
    async with _get_init_lock():
        if _client is not None:  # double-check after acquiring lock
            return _client
        settings = get_settings()
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
        logger.info("Gemini client initialised.")
        return _client


async def _get_semaphore() -> asyncio.Semaphore:
    """Return (or create) the concurrency-limiting semaphore (async-safe)."""
    global _semaphore  # noqa: PLW0603
    if _semaphore is not None:
        return _semaphore
    async with _get_init_lock():
        if _semaphore is not None:
            return _semaphore
        settings = get_settings()
        _semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_LLM_CALLS)
        return _semaphore


# ---------------------------------------------------------------------------
# Default generation / safety config
# ---------------------------------------------------------------------------

# Dental content (bleeding gums, extraction pain, etc.) triggers false
# positives at default thresholds.  BLOCK_ONLY_HIGH lets clinical content
# through while still blocking clearly harmful material.
SAFETY_SETTINGS = [
    types.SafetySetting(
        category="HARM_CATEGORY_DANGEROUS_CONTENT",
        threshold="BLOCK_ONLY_HIGH",
    ),
    types.SafetySetting(
        category="HARM_CATEGORY_HARASSMENT",
        threshold="BLOCK_ONLY_HIGH",
    ),
    types.SafetySetting(
        category="HARM_CATEGORY_HATE_SPEECH",
        threshold="BLOCK_ONLY_HIGH",
    ),
    types.SafetySetting(
        category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
        threshold="BLOCK_ONLY_HIGH",
    ),
]


def build_config(
    *,
    tools: list[types.Tool] | None = None,
    system_instruction: str | None = None,
    temperature: float = 0.4,
    top_p: float = 0.9,
    max_output_tokens: int = 1024,
) -> types.GenerateContentConfig:
    """Build a ``GenerateContentConfig`` with our defaults.

    The new SDK merges generation params, safety, tools, and system
    instruction into a single config object.
    """
    return types.GenerateContentConfig(
        temperature=temperature,
        top_p=top_p,
        max_output_tokens=max_output_tokens,
        safety_settings=SAFETY_SETTINGS,
        tools=tools,
        system_instruction=system_instruction,
    )


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------

def _is_retryable(exc: Exception) -> bool:
    """Return True for transient errors worth retrying."""
    if isinstance(exc, google_exceptions.ResourceExhausted):  # 429
        return True
    if isinstance(exc, google_exceptions.InternalServerError):  # 500
        return True
    if isinstance(exc, google_exceptions.ServiceUnavailable):  # 503
        return True
    if isinstance(exc, google_exceptions.DeadlineExceeded):  # timeout
        return True
    return False


# ---------------------------------------------------------------------------
# Core call helpers
# ---------------------------------------------------------------------------

async def call_gemini(
    *,
    contents: list[types.Content | dict],
    config: types.GenerateContentConfig,
    model: str = MODEL_NAME,
) -> types.GenerateContentResponse:
    """Send a request to Gemini with semaphore gating and retry logic.

    The semaphore is released between retries so backoff sleep doesn't
    waste concurrency capacity.  Jitter is added to avoid thundering herd.
    """
    client = await _get_client()
    sem = await _get_semaphore()
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES + 1):
        async with sem:
            try:
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=model,
                        contents=contents,
                        config=config,
                    ),
                    timeout=_CALL_TIMEOUT,
                )
                return response
            except Exception as exc:
                last_exc = exc
                if not _is_retryable(exc) or attempt == _MAX_RETRIES:
                    logger.error(
                        "Gemini call failed (attempt %d/%d): %s",
                        attempt + 1,
                        _MAX_RETRIES + 1,
                        exc,
                    )
                    raise

        # Sleep OUTSIDE semaphore — don't waste a slot while waiting
        wait = _BACKOFF_BASE * (2 ** attempt) * (0.5 + random.random())
        logger.warning(
            "Gemini retryable error (attempt %d/%d), waiting %.1fs: %s",
            attempt + 1,
            _MAX_RETRIES + 1,
            wait,
            last_exc,
        )
        await asyncio.sleep(wait)

    raise last_exc  # type: ignore[misc]


async def call_gemini_stream(
    *,
    contents: list[types.Content | dict],
    config: types.GenerateContentConfig,
    model: str = MODEL_NAME,
) -> AsyncIterator[types.GenerateContentResponse]:
    """Streaming variant — yields response chunks as they arrive.

    No retry on streaming calls: if the stream fails mid-way, retrying
    would yield duplicate partial content to the caller.  The semaphore
    is held for the full stream duration to respect rate limits.
    """
    client = await _get_client()
    sem = await _get_semaphore()

    async with sem:
        async for chunk in client.aio.models.generate_content_stream(
            model=model,
            contents=contents,
            config=config,
        ):
            yield chunk
