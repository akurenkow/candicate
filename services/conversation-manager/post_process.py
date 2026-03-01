"""Post-process generated response: translate back to user language, semantic fidelity, retry/escalation."""

import logging
from dataclasses import dataclass
from typing import Optional

from config import (
    MODEL_WORKING_LANGUAGE,
    POSTPROCESS_TRANSLATION_RETRY_LIMIT,
    POSTPROCESS_FIDELITY_THRESHOLD,
    POSTPROCESS_FIDELITY_THRESHOLD_CROSSLINGUAL,
)
from embedding_client import embed_batch as embed_batch_client
from translation_client import translate as translate_client

logger = logging.getLogger(__name__)


@dataclass
class PostProcessResult:
    """Stored = model output (generation lang). Translated = for user (user lang), None if not needed or failed."""
    stored: str
    translated: Optional[str]
    translation_failed: bool = False
    translation_failure_reason: Optional[str] = None


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def _validate_fidelity(
    original: str, translated: str, trace_id: str, cross_lingual: bool = False
) -> bool:
    """Semantic validation: embedding similarity >= threshold. Use lower threshold for cross-lingual."""
    original = (original or "").strip()
    translated = (translated or "").strip()
    if not original or not translated:
        return False
    vectors = await embed_batch_client([original, translated], trace_id=trace_id)
    if not vectors or len(vectors) != 2:
        return False
    threshold = (
        POSTPROCESS_FIDELITY_THRESHOLD_CROSSLINGUAL if cross_lingual else POSTPROCESS_FIDELITY_THRESHOLD
    )
    return _cosine(vectors[0], vectors[1]) >= threshold


async def post_process(
    assistant_text_raw: str,
    detected_lang: Optional[str],
    trace_id: str = "",
) -> PostProcessResult:
    """
    If user lang == model lang: return (raw, None).
    Else: translate raw to user lang, validate fidelity with retries; on persistent failure set translation_failed.
    Stored output is always assistant_text_raw (generation language). Translated is for user.
    """
    stored = (assistant_text_raw or "").strip()
    if not stored:
        return PostProcessResult(stored="", translated=None, translation_failed=False)
    user_lang = (detected_lang or MODEL_WORKING_LANGUAGE).lower()
    if user_lang == MODEL_WORKING_LANGUAGE:
        return PostProcessResult(stored=stored, translated=None, translation_failed=False)

    last_translation_error: Optional[str] = None
    fidelity_failed = False
    for attempt in range(1, POSTPROCESS_TRANSLATION_RETRY_LIMIT + 1):
        trans = await translate_client(
            stored, MODEL_WORKING_LANGUAGE, user_lang, trace_id=trace_id
        )
        if trans.error or not (trans.translated_text or "").strip():
            last_translation_error = trans.error or "empty_response"
            logger.warning("post_process translate attempt %s failed: %s", attempt, last_translation_error)
            continue
        translated = trans.translated_text.strip()
        cross_lingual = user_lang != MODEL_WORKING_LANGUAGE
        if await _validate_fidelity(stored, translated, trace_id, cross_lingual=cross_lingual):
            return PostProcessResult(stored=stored, translated=translated, translation_failed=False)
        fidelity_failed = True
        logger.warning("post_process fidelity check failed attempt %s", attempt)
    reason = "fidelity_check_failed" if fidelity_failed and not last_translation_error else (last_translation_error or "unknown")
    return PostProcessResult(stored=stored, translated=None, translation_failed=True, translation_failure_reason=reason)
