"""FLOW-03: Match with Vacancy. Retrieval + optional generation, no new services."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

# Strip Markdown/formatting chars from LLM summary so output is plain text (Telegram, API).
_MARKDOWN_STRIP_RE = re.compile(r"[*#_`\[\]()]+")

from config import GENERATE_MAX_CHUNK, MATCHING_REVIEW_MIN_SIZE, MATCHING_REVIEW_MAX_SIZE
from retrieval_client import search as retrieval_search
from generation_client import generate as generation_generate
from translation_client import translate as translation_translate
from shared.telemetry import record_rag_pipeline_duration, record_retrieval_duration, record_generation_duration

logger = logging.getLogger(__name__)
SERVICE_NAME = "conversation-manager"


def _relevance_score_for_chunks(chunks: list[dict[str, Any]]) -> float:
    """Average of chunk scores, 0..1. Returns 0.0 if empty."""
    if not chunks:
        return 0.0
    scores = [float(c.get("score", 0) or 0) for c in chunks]
    avg = sum(scores) / len(scores)
    return round(min(1.0, max(0.0, avg)), 2)


def _matched_snippets(chunks: list[dict[str, Any]], max_snippets: int = 10) -> list[str]:
    """Best chunk texts for summary, up to max_snippets."""
    with_text = [(c.get("score", 0) or 0, (c.get("text") or "").strip()) for c in chunks]
    with_text = [(s, t) for s, t in with_text if t]
    with_text.sort(key=lambda x: -x[0])
    return [t for _, t in with_text[:max_snippets]]


def _sanitize_summary_text(text: str) -> str:
    """Remove Markdown/formatting chars so match summary is plain text for API and Telegram."""
    if not (text or "").strip():
        return (text or "").strip()
    out = _MARKDOWN_STRIP_RE.sub("", (text or "").strip())
    return re.sub(r" +", " ", out).strip()


async def run_match_vacancy(
    owner_id: str,
    vacancy_text: str,
    include_summary: bool,
    trace_id: str = "",
    top_k: int = 10,
    target_lang: str = "en",
) -> list[dict[str, Any]]:
    """
    Match one candidate (owner_id) to vacancy_text. Retrieval is filtered by owner_id; one relevance_score and result.
    If include_summary and target_lang is 'ru', summary is translated en→ru. Returns list of one item
    { owner_id, relevance_score, summary?, matched_snippets } or [].
    """
    owner_id = (owner_id or "").strip()
    vacancy_text = (vacancy_text or "").strip()
    if not vacancy_text:
        return []

    filters = {"owner_id": owner_id} if owner_id else None
    t_pipeline = time.perf_counter()
    try:
        t0 = time.perf_counter()
        chunks = await retrieval_search(
            query=vacancy_text,
            trace_id=trace_id,
            top_k=top_k,
            filters=filters,
        )
        record_retrieval_duration(time.perf_counter() - t0, "match", SERVICE_NAME)
    except Exception:
        logger.warning("match_vacancy: retrieval failed", exc_info=True)
        return []
    if not chunks:
        record_rag_pipeline_duration(time.perf_counter() - t_pipeline, "match", SERVICE_NAME)
        return []

    relevance = _relevance_score_for_chunks(chunks)
    snippets = _matched_snippets(chunks)
    summary_val: str | None = None
    summary_error: str | None = None
    if include_summary and snippets:
        parts: list[str] = []
        vacancy_short = vacancy_text[:2000]
        context = "\n\n".join(snippets[:8])[:6000]
        prompts = [
            _prompt_general_opinion(vacancy_short, context, relevance),
            _prompt_strong_sides(vacancy_short, context),
            _prompt_weak_points(vacancy_short, context),
        ]
        for i, prompt in enumerate(prompts):
            t0 = time.perf_counter()
            gen = await generation_generate(
                prompt,
                context_query=vacancy_text,
                trace_id=trace_id,
                max_tokens=GENERATE_MAX_CHUNK,
            )
            record_generation_duration(time.perf_counter() - t0, "match", SERVICE_NAME)
            if not gen.success or not (gen.output or "").strip():
                summary_error = gen.error or "match_summary_chunk_failed"
                logger.warning("match_vacancy: chunk %s failed: %s", i + 1, summary_error)
                break
            parts.append(gen.output.strip())
        if not summary_error and parts:
            summary_val = _build_match_summary_headers().format(
                general=parts[0], strong=parts[1], weak=parts[2]
            )
            summary_val = _sanitize_summary_text(summary_val)
            if summary_val and (target_lang or "en").lower() == "ru":
                tr = await translation_translate(summary_val, "en", "ru", trace_id=trace_id)
                if tr.error or not (tr.translated_text or "").strip():
                    logger.warning("match_vacancy: summary translation en→ru failed: %s", tr.error)
                    summary_error = "match_summary_translation_failed"
                else:
                    summary_val = _sanitize_summary_text(tr.translated_text.strip())
    record_rag_pipeline_duration(time.perf_counter() - t_pipeline, "match", SERVICE_NAME)
    result: dict[str, Any] = {
        "owner_id": owner_id,
        "relevance_score": relevance,
        "matched_snippets": snippets,
        **({"summary": summary_val} if summary_val is not None else {}),
    }
    if summary_error is not None:
        result["error"] = summary_error
    return [result]


def _prompt_general_opinion(vacancy: str, context: str, relevance_score: float) -> str:
    return (
        "Given the vacancy and candidate excerpts, write ONE short paragraph in English: overall fit for this vacancy. "
        "End with the relevance score as a number like 0.81 (use period as decimal). The relevance score is {score}.\n\n"
        "VACANCY:\n{vacancy}\n\nCANDIDATE EXCERPTS:\n{context}\n\n"
        "Output only the paragraph, no header."
    ).format(score=relevance_score, vacancy=vacancy, context=context)


def _prompt_strong_sides(vacancy: str, context: str) -> str:
    return (
        "Given the vacancy and candidate excerpts, output in English a bullet list: "
        '"Strong sides (how the candidate is useful for this vacancy)". '
        "Only the section content, no other header.\n\nVACANCY:\n{vacancy}\n\nCANDIDATE EXCERPTS:\n{context}"
    ).format(vacancy=vacancy, context=context)


def _prompt_weak_points(vacancy: str, context: str) -> str:
    return (
        "Given the vacancy and candidate excerpts, output in English a bullet list: "
        '"Weak points (missing information and what to pay attention to first)". '
        "Only the section content, no other header.\n\nVACANCY:\n{vacancy}\n\nCANDIDATE EXCERPTS:\n{context}"
    ).format(vacancy=vacancy, context=context)


def _build_match_summary_headers() -> str:
    """Section headers for assembling three chunks into one message."""
    return (
        "1. General opinion\n{general}\n\n"
        "2. Strong sides (how the candidate is useful for this vacancy)\n{strong}\n\n"
        "3. Weak points (missing information and what to pay attention to first)\n{weak}"
    )
