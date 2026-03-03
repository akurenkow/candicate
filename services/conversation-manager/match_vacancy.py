"""FLOW-03: Match with Vacancy. Retrieval + optional generation, no new services."""

from __future__ import annotations

import logging
import time
from typing import Any

from config import MATCHING_REVIEW_MIN_SIZE, MATCHING_REVIEW_MAX_SIZE
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
    if include_summary and snippets:
        prompt = _build_vacancy_summary_prompt(
            vacancy_text, snippets, relevance,
            min_chars=MATCHING_REVIEW_MIN_SIZE,
            max_chars=MATCHING_REVIEW_MAX_SIZE,
        )
        t0 = time.perf_counter()
        gen = await generation_generate(
            prompt,
            context_query=vacancy_text,
            trace_id=trace_id,
        )
        record_generation_duration(time.perf_counter() - t0, "match", SERVICE_NAME)
        if gen.success and (gen.output or "").strip():
            summary_val = gen.output.strip()
            if summary_val and (target_lang or "en").lower() == "ru":
                tr = await translation_translate(summary_val, "en", "ru", trace_id=trace_id)
                if tr.error or not (tr.translated_text or "").strip():
                    logger.warning("match_vacancy: summary translation en→ru failed: %s", tr.error)
                else:
                    summary_val = tr.translated_text.strip()
            if summary_val and len(summary_val) > MATCHING_REVIEW_MAX_SIZE:
                summary_val = summary_val[:MATCHING_REVIEW_MAX_SIZE].rstrip()
                if summary_val and summary_val[-1].isalnum():
                    last_space = summary_val.rfind(" ")
                    summary_val = summary_val[:last_space].rstrip() if last_space > 0 else summary_val
    record_rag_pipeline_duration(time.perf_counter() - t_pipeline, "match", SERVICE_NAME)
    result = {
        "owner_id": owner_id,
        "relevance_score": relevance,
        "matched_snippets": snippets,
        **({"summary": summary_val} if summary_val is not None else {}),
    }
    return [result]


def _build_vacancy_summary_prompt(
    vacancy_text: str,
    snippets: list[str],
    relevance_score: float,
    min_chars: int = 500,
    max_chars: int = 1500,
) -> str:
    """Prompt for structured match report: general opinion, pros, cons. Output in English with explicit section headers.
    min_chars/max_chars constrain total report length (from .env MATCHING_REVIEW_MIN_SIZE / MATCHING_REVIEW_MAX_SIZE).
    """
    context = "\n\n".join(snippets[:8])
    return (
        "Given the vacancy description and the candidate resume excerpts below, produce a short report in English.\n\n"
        "VACANCY:\n{vacancy}\n\n"
        "CANDIDATE EXCERPTS:\n{context}\n\n"
        "LENGTH: Keep the entire report between {min_chars} and {max_chars} characters (count all three sections).\n\n"
        "Output exactly three sections with these exact headers (copy the headers as-is):\n\n"
        "1. General opinion\n"
        "One short paragraph: overall fit for this vacancy. End with the relevance score as a number like 0.81 or 0.75 (use period as decimal separator). The relevance score is {score}.\n\n"
        "2. Strong sides (how the candidate is useful for this vacancy)\n"
        "Bullet list of candidate strengths and how they match the vacancy.\n\n"
        "3. Weak points (missing information and what to pay attention to first)\n"
        "Bullet list of gaps, missing information, or points to verify first."
    ).format(
        vacancy=vacancy_text[:2000],
        context=context[:6000],
        score=relevance_score,
        min_chars=min_chars,
        max_chars=max_chars,
    )
