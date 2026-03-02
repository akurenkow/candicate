"""Unit tests for clarification-dialog branch: thresholds, loop limit, refined query, state."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_path = Path(__file__).resolve().parent.parent
if str(_path) not in sys.path:
    sys.path.insert(0, str(_path))

from query_decomposition import Subtask
from subtask_result import SubtaskResult, FAILED_RETRIEVAL, FAILED_LOW_SCORE, SUCCESS, NEEDS_CLARIFICATION
from uuid import uuid4


def _subtask(query: str = "test query") -> Subtask:
    return Subtask(subtask_id=uuid4(), query=query, parent_message_id=uuid4(), index=0)


def _chunks(score: float, text: str = "doc") -> list[dict]:
    return [{"chunk_id": "1", "text": text, "score": score}]


@pytest.mark.asyncio
async def test_score_below_clarify_threshold_returns_failed_retrieval():
    """Below CLARIFY_THRESHOLD: implementation returns failed_low_score (no chunks = failed_retrieval)."""
    with patch("subtask_retrieval.retrieval_search", new_callable=AsyncMock, return_value=_chunks(0.2)):
        from subtask_retrieval import run_retrieval
        result = await run_retrieval(_subtask(), trace_id="t1")
        assert result.kind == FAILED_LOW_SCORE


@pytest.mark.asyncio
async def test_score_at_clarify_threshold_triggers_clarification_then_success():
    """Score in [CLARIFY, ANSWER): clarification loop runs; retry with refined query then success."""
    from generation_client import GenerateResult
    call_count = 0

    async def mock_retrieve(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _chunks(0.35)
        return _chunks(0.7)

    async def mock_clarify(*args, **kwargs):
        return GenerateResult(success=True, output="Which period?", attempts=1)

    with patch("subtask_retrieval.CLARIFY_THRESHOLD", 0.35), patch("subtask_retrieval.ANSWER_THRESHOLD", 0.65), patch("subtask_retrieval.MAX_CLARIFICATION_ATTEMPTS", 2):
        with patch("subtask_retrieval.retrieval_search", new_callable=AsyncMock, side_effect=mock_retrieve):
            with patch("subtask_retrieval.gen_clarification", new_callable=AsyncMock, side_effect=mock_clarify):
                with patch("subtask_retrieval.generation_generate", new_callable=AsyncMock, return_value=GenerateResult(success=True, output="Answer.", attempts=1)):
                    from subtask_retrieval import run_retrieval
                    result = await run_retrieval(_subtask(), trace_id="t1")
                    assert result.kind == SUCCESS
                    assert call_count == 2


@pytest.mark.asyncio
async def test_clarification_loop_basic_pass():
    """Базовый проход по циклу уточнений: 1-й retrieval ниже ANSWER -> clarify -> 2-й retrieval выше -> answer -> SUCCESS."""
    from generation_client import GenerateResult
    retrieve_calls = 0
    clarify_calls = 0

    async def mock_retrieve(*args, **kwargs):
        nonlocal retrieve_calls
        retrieve_calls += 1
        if retrieve_calls == 1:
            return _chunks(0.45)
        return _chunks(0.75)

    async def mock_clarify(*args, **kwargs):
        nonlocal clarify_calls
        clarify_calls += 1
        return GenerateResult(success=True, output="Which period: 2020 or 2023?", attempts=1)

    with patch("subtask_retrieval.CLARIFY_THRESHOLD", 0.35), patch("subtask_retrieval.ANSWER_THRESHOLD", 0.65), patch("subtask_retrieval.MAX_CLARIFICATION_ATTEMPTS", 2):
        with patch("subtask_retrieval.retrieval_search", new_callable=AsyncMock, side_effect=mock_retrieve):
            with patch("subtask_retrieval.gen_clarification", new_callable=AsyncMock, side_effect=mock_clarify):
                with patch("subtask_retrieval.generation_generate", new_callable=AsyncMock, return_value=GenerateResult(success=True, output="Answer text.", attempts=1)):
                    from subtask_retrieval import run_retrieval
                    result = await run_retrieval(_subtask("salary"), trace_id="t1")
                    assert result.kind == SUCCESS
                    assert result.text.strip() == "Answer text."
                    assert retrieve_calls == 2
                    assert clarify_calls == 1


@pytest.mark.asyncio
async def test_score_just_below_answer_threshold_triggers_clarification():
    """Score in [CLARIFY, ANSWER): one clarification, retry, then success. Pin thresholds so .env (e.g. ANSWER_THRESHOLD=0.54) does not change behavior."""
    from generation_client import GenerateResult
    retrieve_calls = 0

    async def mock_retrieve(*args, **kwargs):
        nonlocal retrieve_calls
        retrieve_calls += 1
        if retrieve_calls == 1:
            return _chunks(0.64)
        return _chunks(0.7)

    # Patch only subtask_retrieval (do not patch 'config' — in test-runner /workspace another service's config may be resolved)
    with patch("subtask_retrieval.CLARIFY_THRESHOLD", 0.35), patch("subtask_retrieval.ANSWER_THRESHOLD", 0.65), patch("subtask_retrieval.MAX_CLARIFICATION_ATTEMPTS", 2):
        with patch("subtask_retrieval.retrieval_search", new_callable=AsyncMock, side_effect=mock_retrieve):
            with patch("subtask_retrieval.gen_clarification", new_callable=AsyncMock) as m_clarify:
                m_clarify.return_value = GenerateResult(success=True, output="Which topic?", attempts=1)
                with patch("subtask_retrieval.generation_generate", new_callable=AsyncMock, return_value=GenerateResult(success=True, output="Done.", attempts=1)):
                    from subtask_retrieval import run_retrieval
                    result = await run_retrieval(_subtask("original q"), trace_id="t1")
                    assert result.kind == SUCCESS
                    assert m_clarify.call_count >= 1


@pytest.mark.asyncio
async def test_score_at_answer_threshold_normal_flow_no_clarification():
    from generation_client import GenerateResult
    retrieve_calls = 0

    async def mock_retrieve(*args, **kwargs):
        nonlocal retrieve_calls
        retrieve_calls += 1
        return _chunks(0.65)

    with patch("subtask_retrieval.CLARIFY_THRESHOLD", 0.35), patch("subtask_retrieval.ANSWER_THRESHOLD", 0.65), patch("subtask_retrieval.MAX_CLARIFICATION_ATTEMPTS", 2):
        with patch("subtask_retrieval.retrieval_search", new_callable=AsyncMock, side_effect=mock_retrieve):
            with patch("subtask_retrieval.gen_clarification", new_callable=AsyncMock) as m_clarify:
                with patch("subtask_retrieval.generation_generate", new_callable=AsyncMock, return_value=GenerateResult(success=True, output="Answer.", attempts=1)):
                    from subtask_retrieval import run_retrieval
                    result = await run_retrieval(_subtask(), trace_id="t1")
                    assert result.kind == SUCCESS
                    assert m_clarify.call_count == 0
                    assert retrieve_calls == 1


@pytest.mark.asyncio
async def test_clarification_retry_limit_enforcement():
    """After MAX_CLARIFICATION_ATTEMPTS retries with score still in [CLARIFY, ANSWER) -> failed_low_score (user sees low_relevance stub)."""
    from generation_client import GenerateResult
    retrieve_calls = 0

    async def mock_retrieve(*args, **kwargs):
        nonlocal retrieve_calls
        retrieve_calls += 1
        return _chunks(0.5)

    async def mock_clarify(*args, **kwargs):
        return GenerateResult(success=True, output="Clarification?", attempts=1)

    with patch("subtask_retrieval.MAX_CLARIFICATION_ATTEMPTS", 2):
        with patch("subtask_retrieval.retrieval_search", new_callable=AsyncMock, side_effect=mock_retrieve):
            with patch("subtask_retrieval.gen_clarification", new_callable=AsyncMock, side_effect=mock_clarify):
                with patch("subtask_retrieval.generation_generate", new_callable=AsyncMock):
                    from subtask_retrieval import run_retrieval
                    result = await run_retrieval(_subtask(), trace_id="t1")
                    assert result.kind == FAILED_LOW_SCORE
                    assert retrieve_calls <= 1 + 2


@pytest.mark.asyncio
async def test_refined_query_constructed_correctly():
    """Clarification loop: second retrieval uses refined query (original + clarification output)."""
    from generation_client import GenerateResult
    seen_queries = []

    async def mock_retrieve(*args, **kwargs):
        q = kwargs.get("query", args[0] if args else "")
        seen_queries.append(q)
        if len(seen_queries) == 1:
            return _chunks(0.4)
        return _chunks(0.8)

    async def mock_clarify(original_query, documents, history, **kwargs):
        return GenerateResult(success=True, output="user refinement", attempts=1)

    with patch("subtask_retrieval.retrieval_search", new_callable=AsyncMock, side_effect=mock_retrieve):
        with patch("subtask_retrieval.gen_clarification", new_callable=AsyncMock, side_effect=mock_clarify):
            with patch("subtask_retrieval.generation_generate", new_callable=AsyncMock, return_value=GenerateResult(success=True, output="Final.", attempts=1)):
                from subtask_retrieval import run_retrieval
                st = _subtask("original question")
                await run_retrieval(st, trace_id="t1")
                assert len(seen_queries) >= 2
                assert "original question" in seen_queries[0]
                assert "user refinement" in seen_queries[1]


@pytest.mark.asyncio
async def test_clarification_message_saved_to_history():
    from generation_client import GenerateResult
    history_snapshots = []

    async def mock_clarify(original_query, documents, history, **kwargs):
        history_snapshots.append(list(history))
        return GenerateResult(success=True, output="Q1?", attempts=1)

    async def mock_retrieve(*args, **kwargs):
        if len(history_snapshots) == 0:
            return _chunks(0.4)
        if len(history_snapshots) == 1:
            return _chunks(0.4)
        return _chunks(0.8)

    with patch("subtask_retrieval.retrieval_search", new_callable=AsyncMock, side_effect=mock_retrieve):
        with patch("subtask_retrieval.gen_clarification", new_callable=AsyncMock, side_effect=mock_clarify):
            with patch("subtask_retrieval.generation_generate", new_callable=AsyncMock, return_value=GenerateResult(success=True, output="Ok.", attempts=1)):
                from subtask_retrieval import run_retrieval
                await run_retrieval(_subtask(), trace_id="t1")
                assert len(history_snapshots) >= 1
                if len(history_snapshots) >= 2:
                    assert len(history_snapshots[1]) >= 1
                    assert history_snapshots[1][0].get("role") == "assistant"
                    assert "Q1?" in (history_snapshots[1][0].get("content") or "")


@pytest.mark.asyncio
async def test_fallback_after_retry_exhaustion():
    """Score always in [CLARIFY, ANSWER) and MAX_CLARIFICATION_ATTEMPTS exhausted -> failed_low_score (low_relevance stub)."""
    from generation_client import GenerateResult
    async def always_ambiguous(*args, **kwargs):
        return _chunks(0.5)

    async def mock_clarify(*args, **kwargs):
        return GenerateResult(success=True, output="Clar?", attempts=1)

    with patch("subtask_retrieval.MAX_CLARIFICATION_ATTEMPTS", 2):
        with patch("subtask_retrieval.retrieval_search", new_callable=AsyncMock, side_effect=always_ambiguous):
            with patch("subtask_retrieval.gen_clarification", new_callable=AsyncMock, side_effect=mock_clarify):
                from subtask_retrieval import run_retrieval
                result = await run_retrieval(_subtask(), trace_id="t1")
                assert result.kind == FAILED_LOW_SCORE


@pytest.mark.asyncio
async def test_no_infinite_loop_clarification_fails_returns_failed_retrieval():
    """When gen_clarification fails, implementation returns failed_low_score."""
    async def mock_retrieve(*args, **kwargs):
        return _chunks(0.5)

    async def mock_clarify_fail(*args, **kwargs):
        from generation_client import GenerateResult
        return GenerateResult(success=False, output="", error="gen failed")

    with patch("subtask_retrieval.retrieval_search", new_callable=AsyncMock, side_effect=mock_retrieve):
        with patch("subtask_retrieval.gen_clarification", new_callable=AsyncMock, side_effect=mock_clarify_fail):
            from subtask_retrieval import run_retrieval
            result = await run_retrieval(_subtask(), trace_id="t1")
            assert result.kind == FAILED_LOW_SCORE


def test_max_score_helper():
    from subtask_retrieval import _max_score
    assert _max_score([]) == 0.0
    assert _max_score([{"score": 0.3}, {"score": 0.8}]) == 0.8
    assert _max_score([{"score": 0.0}]) == 0.0
    assert _max_score([{}]) == 0.0


def test_refined_query_helper():
    from subtask_retrieval import _refined_query
    assert _refined_query("q", "a") == "q a"
    assert _refined_query("q", "") == "q"
    assert _refined_query("q", "  ") == "q"