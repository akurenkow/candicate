"""Translation API: HTTP and contract for queue-based tasks."""

import asyncio
import logging
import os
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from config import DEFAULT_MODEL, DEFAULT_TEMPERATURE
from ollama_client import translate_via_ollama
from cache import get_cached, set_cached
from shared.tracing import OP_CACHE_READ, OP_CACHE_WRITE, OP_LLM_CALL, log_trace

logger = logging.getLogger(__name__)
SERVICE_NAME = os.getenv("SERVICE_NAME", "translation-service")

router = APIRouter(prefix="", tags=["translation"])


class TranslateRequest(BaseModel):
    """Request body for translation."""

    text: str = Field(..., min_length=0)
    source_lang: str = Field(default="ru", max_length=10)
    target_lang: str = Field(default="en", max_length=10)
    model: Optional[str] = Field(default=None, max_length=64)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)


class TranslateResponse(BaseModel):
    """Response with translated text."""

    translated_text: str
    source_lang: str
    target_lang: str
    model: str
    cached: bool = False


def _translate(
    text: str,
    source_lang: str = "ru",
    target_lang: str = "en",
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    trace_id: str = "",
) -> Tuple[str, bool]:
    """Return (translated_text, cached)."""
    text = (text or "").strip()
    if not text:
        return text, False

    model = model or DEFAULT_MODEL
    log_trace(
        SERVICE_NAME,
        trace_id,
        OP_CACHE_READ,
        "redis",
        target_resource="translation_cache",
    )
    cached = get_cached(text, source_lang, target_lang, model)
    if cached is not None:
        return cached, True

    log_trace(
        SERVICE_NAME,
        trace_id,
        OP_LLM_CALL,
        "ollama",
        target_resource=model,
    )
    result = translate_via_ollama(
        text,
        source_lang=source_lang,
        target_lang=target_lang,
        model=model,
        temperature=temperature if temperature is not None else DEFAULT_TEMPERATURE,
    )
    log_trace(
        SERVICE_NAME,
        trace_id,
        OP_CACHE_WRITE,
        "redis",
        target_resource="translation_cache",
    )
    set_cached(text, source_lang, target_lang, model, result)
    return result, False


@router.post("/translate", response_model=TranslateResponse)
async def translate(body: TranslateRequest, request: Request) -> Dict[str, Any]:
    """
    Translate text using LLM (Ollama).
    Contract: text, source_lang, target_lang, optional model and temperature.
    Results are cached in Redis.
    """
    trace_id = getattr(request.state, "request_id", "") or ""
    try:
        loop = asyncio.get_event_loop()
        translated, cached = await loop.run_in_executor(
            None,
            lambda: _translate(
                body.text,
                source_lang=body.source_lang,
                target_lang=body.target_lang,
                model=body.model,
                temperature=body.temperature,
                trace_id=trace_id,
            ),
        )
        return {
            "translated_text": translated,
            "source_lang": body.source_lang,
            "target_lang": body.target_lang,
            "model": body.model or DEFAULT_MODEL,
            "cached": cached,
        }
    except Exception as e:
        logger.exception("Translation failed: %s", e)
        raise HTTPException(status_code=502, detail="Translation failed") from e
