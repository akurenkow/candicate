"""Generate Executive Summary (cover letter) from candidate resume RAG + vacancy description.
Vacancy and letter in English for RAG/generation; translation-service used only when vacancy is not English
(vacancy -> EN for input, letter EN -> vacancy lang for output)."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from retrieval_client import search as retrieval_search
from generation_client import generate as generation_generate
from translation_client import translate as translation_translate
from lang_detect import detect as lang_detect
from shared.telemetry import record_rag_pipeline_duration, record_retrieval_duration, record_generation_duration

from config import (
    COVER_LETTER_MAX_SIZE,
    COVER_LETTER_MIN_SIZE,
    COVER_LETTER_PROMPT_PATH,
    RETRIEVAL_TOP_K,
)

logger = logging.getLogger(__name__)
SERVICE_NAME = "conversation-manager"


def _load_system_prompt() -> str:
    path = Path(COVER_LETTER_PROMPT_PATH)
    if not path.is_absolute():
        base = Path(__file__).resolve().parent.parent.parent
        path = base / "prompts" / "executive-summary-cover-letter.txt"
    if not path.exists():
        path = Path(COVER_LETTER_PROMPT_PATH)
    raw = path.read_text(encoding="utf-8") if path.exists() else ""
    if not raw:
        raw = (
            "Write an Executive Summary of the candidate's fit for the vacancy. "
            "Length: {min_chars}-{max_chars} characters. Output in English only. "
            "Use only facts from the candidate excerpts. Output only the text."
        )
    return raw.format(
        min_chars=COVER_LETTER_MIN_SIZE,
        max_chars=COVER_LETTER_MAX_SIZE,
    )


async def run_cover_letter(
    owner_id: str,
    vacancy_text: str,
    trace_id: str = "",
    top_k: int | None = None,
) -> dict[str, Any]:
    """
    RAG: retrieval by vacancy (in English) over owner's docs, then generate Executive Summary in English.
    If vacancy is not English: translate vacancy -> EN for retrieval/generation; then translate letter EN -> vacancy lang.
    If vacancy is English: no translation.
    Returns { "owner_id", "letter" } or { "owner_id", "error" } on failure.
    """
    owner_id = (owner_id or "").strip()
    vacancy_text = (vacancy_text or "").strip()
    if not vacancy_text:
        return {"owner_id": owner_id, "error": "vacancy_text required"}
    vacancy_lang = lang_detect(vacancy_text)
    vacancy_for_rag = vacancy_text
    if vacancy_lang != "en":
        tr = await translation_translate(vacancy_text, vacancy_lang, "en", trace_id=trace_id)
        if tr.error or not (tr.translated_text or "").strip():
            logger.warning("cover_letter: vacancy translation failed lang=%s err=%s", vacancy_lang, tr.error)
            return {"owner_id": owner_id, "error": "vacancy_translation_failed"}
        vacancy_for_rag = tr.translated_text.strip()
    k = top_k if top_k is not None else RETRIEVAL_TOP_K
    filters = {"owner_id": owner_id} if owner_id else None
    t_pipeline = time.perf_counter()
    try:
        t0 = time.perf_counter()
        chunks = await retrieval_search(
            query=vacancy_for_rag,
            trace_id=trace_id,
            top_k=k,
            filters=filters,
        )
        record_retrieval_duration(time.perf_counter() - t0, "cover_letter", SERVICE_NAME)
    except Exception as e:
        logger.warning("cover_letter: retrieval failed: %s", e, exc_info=True)
        record_rag_pipeline_duration(time.perf_counter() - t_pipeline, "cover_letter", SERVICE_NAME)
        return {"owner_id": owner_id, "error": "retrieval_failed"}
    if not chunks:
        record_rag_pipeline_duration(time.perf_counter() - t_pipeline, "cover_letter", SERVICE_NAME)
        return {"owner_id": owner_id, "error": "no_documents"}
    context = "\n\n".join((c.get("text") or "").strip() for c in chunks if (c.get("text") or "").strip())
    if not context:
        record_rag_pipeline_duration(time.perf_counter() - t_pipeline, "cover_letter", SERVICE_NAME)
        return {"owner_id": owner_id, "error": "no_documents"}
    system_prompt = _load_system_prompt()
    user_prompt = (
        "VACANCY DESCRIPTION (English):\n{vacancy}\n\n"
        "CANDIDATE EXCERPTS (use only these facts):\n{context}"
    ).format(vacancy=vacancy_for_rag[:4000], context=context[:8000])
    t0 = time.perf_counter()
    gen = await generation_generate(
        user_prompt,
        context_query=vacancy_for_rag,
        system_prompt=system_prompt,
        trace_id=trace_id,
    )
    record_generation_duration(time.perf_counter() - t0, "cover_letter", SERVICE_NAME)
    if not gen.success or not (gen.output or "").strip():
        record_rag_pipeline_duration(time.perf_counter() - t_pipeline, "cover_letter", SERVICE_NAME)
        return {"owner_id": owner_id, "error": "generation_failed"}
    letter = gen.output.strip()
    if vacancy_lang != "en":
        tr_out = await translation_translate(letter, "en", vacancy_lang, trace_id=trace_id)
        if tr_out.error or not (tr_out.translated_text or "").strip():
            logger.warning("cover_letter: letter translation failed en->%s err=%s", vacancy_lang, tr_out.error)
            record_rag_pipeline_duration(time.perf_counter() - t_pipeline, "cover_letter", SERVICE_NAME)
            return {"owner_id": owner_id, "error": "letter_translation_failed"}
        letter = tr_out.translated_text.strip()
    record_rag_pipeline_duration(time.perf_counter() - t_pipeline, "cover_letter", SERVICE_NAME)
    return {"owner_id": owner_id, "letter": letter}
