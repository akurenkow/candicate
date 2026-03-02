"""Isolated retrieval + generation step for a subtask. No queue/DB dependencies for testability."""

from __future__ import annotations

import difflib
import logging
import time
from typing import Any

from config import (
    MODEL_CONTEXT_LIMIT,
    CLARIFY_THRESHOLD,
    ANSWER_THRESHOLD,
    COMPANY_FUZZY_CUTOFF,
    MAX_CLARIFICATION_ATTEMPTS,
)
from prompt_assembler import build_prompt
from query_decomposition import Subtask
from query_filters import extract_company_from_query
from retrieval_client import search as retrieval_search, get_owner_companies
from generation_client import generate as generation_generate, generate_clarification as gen_clarification
from subtask_result import SubtaskResult
from shared.company_normalize import normalize_company_name
from shared.telemetry import record_retrieval_duration, record_generation_duration

logger = logging.getLogger(__name__)
SERVICE_NAME = "conversation-manager"


def _max_score(chunks: list[dict[str, Any]]) -> float:
    """Max relevance score from chunk list; 0.0 if empty."""
    if not chunks:
        return 0.0
    return max(float(c.get("score", 0) or 0) for c in chunks)


def _refined_query(original: str, refinement: str) -> str:
    """Build query for retry: original + non-empty refinement (space-separated)."""
    if not (refinement or "").strip():
        return (original or "").strip() or ""
    return f"{(original or '').strip()} {(refinement or '').strip()}".strip()


def _resolve_company_fuzzy(normalized: str, candidates: list[str], cutoff: float | None = None) -> str | None:
    """Return best matching stored company name for typo/transliteration (e.g. terkom->tercom), or None."""
    if not normalized or not candidates:
        return None
    if cutoff is None:
        cutoff = COMPANY_FUZZY_CUTOFF
    matches = difflib.get_close_matches(normalized, candidates, n=1, cutoff=cutoff)
    return matches[0] if matches else None


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
    Score in [CLARIFY_THRESHOLD, ANSWER_THRESHOLD): clarification loop — gen_clarification,
    refined query, retry retrieval (up to MAX_CLARIFICATION_ATTEMPTS); on exhaustion -> failed_retrieval.
    Score >= ANSWER_THRESHOLD -> generate answer, return success or failed_generation.
    owner_id scopes search to candidate (documents.owner_id).
    """
    filters: dict[str, str] = {}
    if (owner_id or "").strip():
        filters["owner_id"] = owner_id.strip()
    company_raw = extract_company_from_query(subtask.query)
    if company_raw:
        company_norm = normalize_company_name(company_raw)
        if company_norm:
            companies = await get_owner_companies(owner_id, trace_id) if owner_id else []
            if companies:
                # Resolve typo/transliteration (e.g. terkom→tercom); use stored value in filter for exact match in Qdrant
                pairs = [(normalize_company_name(c), c) for c in companies]
                normalized_list = [p[0] for p in pairs]
                match_norm = _resolve_company_fuzzy(company_norm, normalized_list)
                if match_norm:
                    idx = normalized_list.index(match_norm)
                    resolved = pairs[idx][1]
                else:
                    resolved = company_norm
            else:
                resolved = company_norm
            filters["company_name"] = resolved
    use_filters = filters if filters else None

    query = subtask.query
    history: list[dict[str, str]] = []
    attempt = 0

    while True:
        chunks = await _retrieve(query, trace_id, owner_id, use_filters)

        if not chunks:
            logger.info(
                "run_retrieval no chunks owner_id=%r query=%r attempt=%s -> failed_retrieval (user sees no_documents stub)",
                owner_id,
                (query[:60] if query else ""),
                attempt,
            )
            return SubtaskResult.failed_retrieval()

        max_score_val = _max_score(chunks)
        logger.info(
            "run_retrieval owner_id=%r query=%r attempt=%s chunks_count=%s max_score=%s first_chunk_keys=%s",
            owner_id,
            (query[:60] if query else ""),
            attempt,
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

        if max_score_val < ANSWER_THRESHOLD:
            if attempt >= MAX_CLARIFICATION_ATTEMPTS:
                logger.info(
                    "run_retrieval clarification_exhausted owner_id=%r max_score=%s < ANSWER_THRESHOLD=%s attempt=%s -> failed_low_score (user sees low_relevance stub)",
                    owner_id,
                    max_score_val,
                    ANSWER_THRESHOLD,
                    attempt,
                )
                return SubtaskResult.failed_low_score()
            t0 = time.perf_counter()
            clar = await gen_clarification(subtask.query, chunks, history, trace_id=trace_id)
            record_generation_duration(time.perf_counter() - t0, "query", SERVICE_NAME)
            if not clar.success or not (clar.output or "").strip():
                return SubtaskResult.failed_low_score()
            history.append({"role": "assistant", "content": clar.output.strip()})
            query = _refined_query(query, clar.output)
            attempt += 1
            continue

        # Score >= ANSWER_THRESHOLD: generate full answer
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
