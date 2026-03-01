"""Translation client: call Translation Service with retry; clear error propagation."""

import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from config import (
    TRANSLATION_SERVICE_URL,
    TRANSLATION_RETRY_LIMIT,
    TRANSLATION_TIMEOUT,
)
from shared.tracing import OP_HTTP_OUT, log_trace

logger = logging.getLogger(__name__)
SERVICE_NAME = "conversation-manager"


@dataclass
class TranslateResult:
    translated_text: Optional[str] = None
    error: Optional[str] = None


async def translate(
    text: str,
    source_lang: str,
    target_lang: str,
    trace_id: str = "",
) -> TranslateResult:
    """
    Translate text via Translation Service. Retries up to TRANSLATION_RETRY_LIMIT.
    On failure returns TranslateResult(translated_text=None, error="..."). Does not overwrite original.
    """
    text = (text or "").strip()
    if not text:
        return TranslateResult(translated_text="", error=None)
    url = f"{TRANSLATION_SERVICE_URL.rstrip('/')}/translate"
    last_error: Optional[str] = None
    for attempt in range(1, TRANSLATION_RETRY_LIMIT + 1):
        log_trace(
            SERVICE_NAME,
            trace_id,
            OP_HTTP_OUT,
            "translation-service",
            target_resource="/translate",
        )
        try:
            async with httpx.AsyncClient(timeout=TRANSLATION_TIMEOUT) as client:
                r = await client.post(
                    url,
                    json={
                        "text": text,
                        "source_lang": source_lang,
                        "target_lang": target_lang,
                    },
                    headers={"X-Request-ID": trace_id} if trace_id else {},
                )
                r.raise_for_status()
                data = r.json()
                out = (data.get("translated_text") or text).strip() or text
                return TranslateResult(translated_text=out, error=None)
        except httpx.HTTPStatusError as e:
            last_error = f"translation_http_error:{e.response.status_code}"
            logger.warning("translate attempt %s: %s", attempt, last_error)
        except (httpx.RequestError, Exception) as e:
            last_error = f"translation_error:{type(e).__name__}"
            logger.warning("translate attempt %s: %s", attempt, e)
        if attempt < TRANSLATION_RETRY_LIMIT:
            time.sleep(0.5 * attempt)
    return TranslateResult(translated_text=None, error=last_error or "translation_failed")
