"""Isolated retrieval + generation step for a subtask. No queue/DB dependencies for testability."""

from __future__ import annotations

import logging
import time
from typing import Any

from config import (
    MODEL_CONTEXT_LIMIT,
    CLARIFY_THRESHOLD,
    ANSWER_THRESHOLD,
    MAX_CLARIFICATION_ATTEMPTS,
)
from prompt_assembler import build_prompt
from query_decomposition import Subtask
from query_filters import extract_company_from_query
from retrieval_client import search as retrieval_search
from generation_client import generate as generation_generate
from generation_client import generate_clarification as gen_clarification
from subtask_result import SubtaskResult
from shared.telemetry import record_retrieval_duration, record_generation_duration

logger = logging.getLogger(__name__)
SERVICE_NAME = "conversation-manager"


def _max_score(chunks: list[dict[str, Any]]) -> float:
    """Max relevance score from chunk list; 0.0 if empty."""
    if not chunks:
        return 0.0
    return max(float(c.get("score", 0) or 0) for c in chunks)


def _refined_query(original_query: str, clarification_answer: str) -> str:
    """Merge original query and user clarification into refined query."""
    a = (clarification_answer or "").strip()
    if not a:
        return original_query
    return f"{original_query.strip()} {a}".strip()


async def _retrieve(
    query: str,
    trace_id: str,
    owner_id: str,
    filters: dict[str, str] | None,
) -> list[dict[str, Any]]:
    t0 = time.perf_counter()
    out = await retrieval_search(query=query, trace_id=trace_id, filters=filters)
    record_retrieval_duration(time.perf_counter() - t0, "query", SERVICE_NAME)
    return out


async def run_retrieval(subtask: Subtask, trace_id: str = "", owner_id: str = "") -> SubtaskResult:
    """
    Run retrieval then generation for one subtask.
    Applies score thresholds: below CLARIFY_THRESHOLD -> failed_retrieval;
    between CLARIFY_THRESHOLD and ANSWER_THRESHOLD -> clarification loop (refined query, re-retrieve, bounded retries);
    at or above ANSWER_THRESHOLD -> normal generation.
    owner_id scopes search to candidate (documents.owner_id).
    """
    filters: dict[str, str] = {}
    if (owner_id or "").strip():
        filters["owner_id"] = owner_id.strip()
    company = extract_company_from_query(subtask.query)
    if company:
        filters["company_name"] = company
    use_filters = filters if filters else None

    query = subtask.query
    chunks = await _retrieve(query, trace_id, owner_id, use_filters)

    # If we filtered by company_name and got 0 chunks (e.g. name mismatch in index), retry without company filter
    # so we still return semantic matches instead of the "upload resume" stub.
    if not chunks and use_filters and "company_name" in use_filters:
        fallback_filters = {k: v for k, v in use_filters.items() if k != "company_name"}
        fallback_filters = fallback_filters if fallback_filters else None
        chunks = await _retrieve(query, trace_id, owner_id, fallback_filters)
        if chunks:
            logger.info(
                "run_retrieval company_name filter returned 0 chunks; using %s chunks from fallback (no company filter)",
                len(chunks),
            )

    if not chunks:
        return SubtaskResult.failed_retrieval()

    max_score_val = _max_score(chunks)
    logger.info(
        "run_retrieval owner_id=%r query=%r chunks_count=%s max_score=%s first_chunk_keys=%s",
        owner_id,
        (query[:60] if query else ""),
        len(chunks),
        max_score_val,
        list(chunks[0].keys()) if chunks else [],
    )

    # Chunks with section_kind=general (e.g. salary) are answer-ready; skip clarification loop
    if any((c.get("metadata") or {}).get("section_kind") == "general" for c in chunks):
        max_score_val = max(max_score_val, ANSWER_THRESHOLD)

    if max_score_val < CLARIFY_THRESHOLD:
        return SubtaskResult.failed_retrieval()

    if not any((c.get("text") or "").strip() for c in chunks):
        return SubtaskResult.failed_no_usable_text()

    clarification_attempts = 0
    history: list[dict[str, str]] = []

    while max_score_val < ANSWER_THRESHOLD:
        if clarification_attempts >= MAX_CLARIFICATION_ATTEMPTS:
            return SubtaskResult.failed_retrieval()

        t0 = time.perf_counter()
        clar = await gen_clarification(query, chunks, history, trace_id=trace_id)
        record_generation_duration(time.perf_counter() - t0, "query", SERVICE_NAME)
        if not clar.success or not (clar.output or "").strip():
            return SubtaskResult.failed_retrieval()

        history.append({"role": "assistant", "content": clar.output})
        query = _refined_query(subtask.query, clar.output)
        clarification_attempts += 1

        chunks = await _retrieve(query, trace_id, owner_id, use_filters)
        if not chunks and use_filters and "company_name" in use_filters:
            fallback_filters = {k: v for k, v in use_filters.items() if k != "company_name"}
            fallback_filters = fallback_filters if fallback_filters else None
            chunks = await _retrieve(query, trace_id, owner_id, fallback_filters)
        if not chunks:
            return SubtaskResult.failed_retrieval()
        max_score_val = _max_score(chunks)
        if any((c.get("metadata") or {}).get("section_kind") == "general" for c in chunks):
            max_score_val = max(max_score_val, ANSWER_THRESHOLD)
        if max_score_val < CLARIFY_THRESHOLD:
            return SubtaskResult.failed_retrieval()
        if not any((c.get("text") or "").strip() for c in chunks):
            return SubtaskResult.failed_no_usable_text()

    prompt = build_prompt([], chunks, query, MODEL_CONTEXT_LIMIT)
    t0 = time.perf_counter()
    result = await generation_generate(
        prompt.text,
        context_query=subtask.query,
        system_prompt=prompt.system_instruction,
        validate_verbosity=True,
        trace_id=trace_id,
    )
    record_generation_duration(time.perf_counter() - t0, "query", SERVICE_NAME)
    if not result.success or not result.output:
        return SubtaskResult.failed_generation()
    return SubtaskResult.success(result.output)
