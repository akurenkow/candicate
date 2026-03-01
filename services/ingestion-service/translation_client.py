"""
HTTP client for Translation Service (LLM-based ru->en).
Used in FLOW-02 before chunking when TRANSLATE_TO_ENABLED is true.
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass
class TranslateResult:
    translated_text: Optional[str] = None
    error: Optional[str] = None

from config import MAX_RETRIES, TRANSLATION_SERVICE_URL
from shared.tracing import OP_HTTP_OUT, log_trace

logger = logging.getLogger(__name__)
SERVICE_NAME = "ingestion-service"


async def translate_ru_to_en_async(
    text: str,
    client: httpx.AsyncClient,
    timeout: float = 120.0,
    trace_id: str = "",
) -> str:
    """
    Translate Russian text to English via translation-service.
    Retries on failure and on empty LLM response; returns original only after all retries exhausted.
    """
    text = (text or "").strip()
    if not text:
        return text
    if os.getenv("TRANSLATE_TO_ENABLED", "true").lower() not in ("true", "1", "yes"):
        return text

    url = f"{TRANSLATION_SERVICE_URL.rstrip('/')}/translate"
    headers = {"X-Request-ID": trace_id} if trace_id else {}
    payload = {"text": text, "source_lang": "ru", "target_lang": "en"}
    last_error: Exception | None = None
    max_attempts = max(1, int(MAX_RETRIES) if MAX_RETRIES else 3)

    for attempt in range(max_attempts):
        log_trace(
            SERVICE_NAME,
            trace_id,
            OP_HTTP_OUT,
            "translation-service",
            target_resource="/translate",
        )
        try:
            r = await client.post(url, json=payload, timeout=timeout, headers=headers)
            r.raise_for_status()
            data = r.json()
            out = (data.get("translated_text") or "").strip()
            if out:
                return out
            last_error = ValueError("empty translated_text")
        except Exception as e:
            last_error = e
        if attempt < max_attempts - 1:
            await asyncio.sleep(1.0 * (attempt + 1))
    logger.warning("Translation failed after %s attempts, using original: %s", max_attempts, last_error)
    return text
