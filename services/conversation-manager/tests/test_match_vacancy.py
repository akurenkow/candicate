"""Unit tests for FLOW-03 match-vacancy: scoring, summary conditional, errors."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_path = Path(__file__).resolve().parent.parent
if str(_path) not in sys.path:
    sys.path.insert(0, str(_path))

from generation_client import GenerateResult
from match_vacancy import (
    _relevance_score_for_chunks,
    _matched_snippets,
    run_match_vacancy,
)


def test_relevance_score_for_chunks_empty():
    assert _relevance_score_for_chunks([]) == 0.0


def test_relevance_score_for_chunks_average():
    chunks = [
        {"score": 0.8},
        {"score": 0.4},
    ]
    assert _relevance_score_for_chunks(chunks) == 0.6


def test_relevance_score_for_chunks_capped():
    chunks = [{"score": 1.5}, {"score": -0.1}]
    s = _relevance_score_for_chunks(chunks)
    assert 0.0 <= s <= 1.0


def test_matched_snippets_respects_order_and_limit():
    chunks = [
        {"text": "low", "score": 0.1},
        {"text": "high", "score": 0.9},
        {"text": "mid", "score": 0.5},
    ]
    snippets = _matched_snippets(chunks, max_snippets=2)
    assert snippets == ["high", "mid"]
    assert len(snippets) <= 2


def test_matched_snippets_skips_empty_text():
    chunks = [
        {"text": "ok", "score": 0.8},
        {"text": "  ", "score": 0.7},
    ]
    assert _matched_snippets(chunks) == ["ok"]


@pytest.mark.asyncio
async def test_run_match_vacancy_empty_retrieval_returns_empty_list():
    with patch("match_vacancy.retrieval_search", new_callable=AsyncMock, return_value=[]):
        result = await run_match_vacancy(
            owner_id="cand1",
            vacancy_text="Python developer",
            include_summary=False,
            trace_id="t1",
            top_k=5,
        )
        assert result == []


@pytest.mark.asyncio
async def test_run_match_vacancy_empty_vacancy_text_returns_empty_list():
    result = await run_match_vacancy(
        owner_id="cand1",
        vacancy_text="",
        include_summary=False,
        trace_id="t1",
    )
    assert result == []


@pytest.mark.asyncio
async def test_run_match_vacancy_top_k_passed_to_retrieval():
    chunks = [{"chunk_id": "1", "text": "resume", "score": 0.7, "metadata": {}}]
    with patch("match_vacancy.retrieval_search", new_callable=AsyncMock, return_value=chunks) as m:
        await run_match_vacancy(
            owner_id="u1",
            vacancy_text="vacancy",
            include_summary=False,
            trace_id="t1",
            top_k=7,
        )
        m.assert_called_once()
        assert m.call_args.kwargs.get("top_k") == 7


@pytest.mark.asyncio
async def test_run_match_vacancy_filters_by_owner_id():
    chunks = [{"chunk_id": "1", "text": "x", "score": 0.8, "metadata": {}}]
    with patch("match_vacancy.retrieval_search", new_callable=AsyncMock, return_value=chunks) as m:
        await run_match_vacancy(
            owner_id="hr_candidate_1",
            vacancy_text="Java",
            include_summary=False,
            trace_id="t1",
        )
        m.assert_called_once()
        assert m.call_args.kwargs.get("filters") == {"owner_id": "hr_candidate_1"}


@pytest.mark.asyncio
async def test_run_match_vacancy_no_summary_does_not_call_generation():
    chunks = [{"chunk_id": "1", "text": "snippet", "score": 0.75, "metadata": {}}]
    with patch("match_vacancy.retrieval_search", new_callable=AsyncMock, return_value=chunks):
        with patch("match_vacancy.generation_generate", new_callable=AsyncMock) as gen_mock:
            result = await run_match_vacancy(
                owner_id="u1",
                vacancy_text="vacancy",
                include_summary=False,
                trace_id="t1",
            )
            gen_mock.assert_not_called()
            assert len(result) == 1
            assert result[0]["owner_id"] == "u1"
            assert result[0]["relevance_score"] == 0.75
            assert "summary" not in result[0]
            assert result[0]["matched_snippets"] == ["snippet"]


@pytest.mark.asyncio
async def test_run_match_vacancy_summary_true_invokes_generation():
    chunks = [{"chunk_id": "1", "text": "snippet", "score": 0.8, "metadata": {}}]
    gen_results = [
        GenerateResult(success=True, output="Overall fit 0.80.", attempts=1),
        GenerateResult(success=True, output="- Python\n- SQL", attempts=1),
        GenerateResult(success=True, output="- Gap in X", attempts=1),
    ]

    def side_effect(*args, **kwargs):
        return gen_results.pop(0) if gen_results else gen_results[0]

    with patch("match_vacancy.retrieval_search", new_callable=AsyncMock, return_value=chunks):
        with patch(
            "match_vacancy.generation_generate",
            new_callable=AsyncMock,
            side_effect=gen_results,
        ) as gen_mock:
            result = await run_match_vacancy(
                owner_id="u1",
                vacancy_text="vacancy",
                include_summary=True,
                trace_id="t1",
            )
            assert gen_mock.call_count == 3
            assert len(result) == 1
            assert "1. General opinion\n" in result[0].get("summary", "")
            assert "2. Strong sides" in result[0].get("summary", "")
            assert "3. Weak points" in result[0].get("summary", "")
            assert result[0]["relevance_score"] == 0.8
            assert result[0]["matched_snippets"] == ["snippet"]
            assert "error" not in result[0]


@pytest.mark.asyncio
async def test_run_match_vacancy_scoring_aggregation_multiple_chunks():
    chunks = [
        {"chunk_id": "1", "text": "a", "score": 0.6, "metadata": {}},
        {"chunk_id": "2", "text": "b", "score": 0.8, "metadata": {}},
    ]
    with patch("match_vacancy.retrieval_search", new_callable=AsyncMock, return_value=chunks):
        result = await run_match_vacancy(
            owner_id="u1",
            vacancy_text="vacancy",
            include_summary=False,
            trace_id="t1",
        )
        assert len(result) == 1
        assert result[0]["relevance_score"] == 0.7
        assert result[0]["matched_snippets"] == ["b", "a"]


@pytest.mark.asyncio
async def test_run_match_vacancy_retrieval_exception_returns_empty():
    with patch("match_vacancy.retrieval_search", new_callable=AsyncMock, side_effect=RuntimeError("network")):
        result = await run_match_vacancy(
            owner_id="u1",
            vacancy_text="vacancy",
            include_summary=False,
            trace_id="t1",
        )
        assert result == []


@pytest.mark.asyncio
async def test_run_match_vacancy_generation_failure_still_returns_result_without_summary():
    chunks = [{"chunk_id": "1", "text": "snippet", "score": 0.7, "metadata": {}}]
    with patch("match_vacancy.retrieval_search", new_callable=AsyncMock, return_value=chunks):
        with patch(
            "match_vacancy.generation_generate",
            new_callable=AsyncMock,
            return_value=GenerateResult(success=False, output="", error="timeout"),
        ):
            result = await run_match_vacancy(
                owner_id="u1",
                vacancy_text="vacancy",
                include_summary=True,
                trace_id="t1",
            )
            assert len(result) == 1
            assert result[0]["owner_id"] == "u1"
            assert result[0]["relevance_score"] == 0.7
            assert "summary" not in result[0]
            assert result[0].get("error") == "timeout"
            assert result[0]["matched_snippets"] == ["snippet"]
