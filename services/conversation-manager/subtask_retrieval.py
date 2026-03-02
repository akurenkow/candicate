"""Isolated retrieval + generation step for a subtask. No queue/DB dependencies for testability."""

from __future__ import annotations

import logging
import time
from typing import Any

from config import (
    MODEL_CONTEXT_LIMIT,
    CLARIFY_THRESHOLD,
    ANSWER_THRESHOLD,
)
from prompt_assembler import build_prompt
from query_decomposition import Subtask
from query_filters import extract_company_from_query
from retrieval_client import search as retrieval_search
from generation_client import generate as generation_generate
from subtask_result import SubtaskResult
from shared.telemetry import record_retrieval_duration, record_generation_duration

logger = logging.getLogger(__name__)
SERVICE_NAME = "conversation-manager"


def _max_score(chunks: list[dict[str, Any]]) -> float:
    """Max relevance score from chunk list; 0.0 if empty."""
    if not chunks:
        return 0.0
    return max(float(c.get("score", 0) or 0) for c in chunks)


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
    Below CLARIFY_THRESHOLD -> failed_low_score; no chunks -> failed_retrieval.
    At or above CLARIFY_THRESHOLD with usable chunks -> generate answer (no internal clarification loop).
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
        return SubtaskResult.failed_low_score()

    if not any((c.get("text") or "").strip() for c in chunks):
        return SubtaskResult.failed_no_usable_text()

    # Generate answer from retrieved chunks. Clarification loop disabled: questions were
    # generated internally and never sent to the user, so we always generate when above CLARIFY.
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
