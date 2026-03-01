"""
Embedding API: single embed and embed_batch (CTR-15).
Consumers: Retrieval Service, Ingestion Service.
Uses external_llm factory: OpenAI, DeepSeek, or Ollama.
"""

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from shared.tracing import OP_LLM_CALL, log_trace

logger = logging.getLogger(__name__)
SERVICE_NAME = os.getenv("SERVICE_NAME", "embedding-service")

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "32"))
# Max input chars per request to stay under model context
_EMBED_MAX_INPUT_CHARS_RAW = 1024  # hard cap regardless of env


def _embed_max_input_chars() -> int:
    raw = (os.getenv("EMBED_MAX_INPUT_CHARS") or "512").strip()
    if not raw:
        return 512
    try:
        n = int(raw)
        n = 512 if n <= 0 else min(n, _EMBED_MAX_INPUT_CHARS_RAW)
        return max(256, n)
    except ValueError:
        return 512


EMBED_MAX_INPUT_CHARS = _embed_max_input_chars()

router = APIRouter(prefix="", tags=["embedding"])

_provider_cache = None


def _get_provider():
    """Lazy resolve via external_llm factory."""
    global _provider_cache
    if _provider_cache is None:
        from external_llm.factory import resolve_for_embed
        _provider_cache = resolve_for_embed()
    return _provider_cache


class EmbedRequest(BaseModel):
    """Single text embedding request."""

    text: str = Field(..., min_length=1)


class EmbedBatchRequest(BaseModel):
    """Batch embedding request. Max size enforced in endpoint (BATCH_SIZE env)."""

    texts: list[str] = Field(..., min_length=1)


class EmbedResponse(BaseModel):
    """Single embedding response."""

    vector: list[float]


class EmbedBatchResponse(BaseModel):
    """Batch embedding response."""

    vectors: list[list[float]]


async def _embed_one(text: str, trace_id: str = "") -> list[float]:
    """Get embedding via resolved provider (OpenAI/DeepSeek/Ollama)."""
    if len(text) > EMBED_MAX_INPUT_CHARS:
        text = text[:EMBED_MAX_INPUT_CHARS]
    provider = _get_provider()
    log_trace(
        SERVICE_NAME,
        trace_id,
        OP_LLM_CALL,
        provider.name,
        target_resource="embed",
    )
    try:
        return await provider.embed(text)
    except Exception as e:
        logger.exception("Embed failed: %s", e)
        msg = str(e) if str(e) else "Embedding failed"
        raise HTTPException(status_code=502, detail=f"Embedding failed: {msg}") from e


def _get_trace_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or ""


@router.post("/embed", response_model=EmbedResponse)
async def embed(body: EmbedRequest, request: Request) -> dict[str, Any]:
    """Embed a single text. Contract CTR-15."""
    trace_id = _get_trace_id(request)
    vector = await _embed_one(body.text, trace_id=trace_id)
    return {"vector": vector}


@router.post("/embed_batch", response_model=EmbedBatchResponse)
async def embed_batch(body: EmbedBatchRequest, request: Request) -> dict[str, Any]:
    """
    Embed a batch of texts. Contract CTR-15.
    Limited to BATCH_SIZE (env, default 32).
    """
    if len(body.texts) > BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Too many texts: max {BATCH_SIZE}",
        )
    trace_id = _get_trace_id(request)
    provider = _get_provider()
    log_trace(
        SERVICE_NAME,
        trace_id,
        OP_LLM_CALL,
        provider.name,
        target_resource="embed_batch",
    )
    try:
        # Cap each text to avoid context overflow
        capped = [
            t[:EMBED_MAX_INPUT_CHARS] if len(t) > EMBED_MAX_INPUT_CHARS else t
            for t in body.texts
        ]
        vectors = await provider.embed_batch(capped)
        return {"vectors": vectors}
    except Exception as e:
        logger.exception("Embed batch failed: %s", e)
        msg = str(e) if str(e) else "Embedding failed"
        raise HTTPException(status_code=502, detail=f"Embedding failed: {msg}") from e
