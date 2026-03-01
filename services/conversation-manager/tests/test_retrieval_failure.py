"""Retrieval integration: no documents -> failed_retrieval, aggregate -> fallback + escalated."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_path = Path(__file__).resolve().parent.parent
if str(_path) not in sys.path:
    sys.path.insert(0, str(_path))

from query_decomposition import Subtask
from subtask_result import SubtaskResult, aggregate_results, FAILED_RETRIEVAL
from uuid import uuid4


@pytest.mark.asyncio
async def test_run_retrieval_no_chunks_returns_failed_retrieval():
    with patch("subtask_retrieval.retrieval_search", new_callable=AsyncMock, return_value=[]):
        from subtask_retrieval import run_retrieval
        st = Subtask(subtask_id=uuid4(), query="test query", parent_message_id=uuid4(), index=0)
        result = await run_retrieval(st, trace_id="trace-1")
        assert result.kind == FAILED_RETRIEVAL


@pytest.mark.asyncio
async def test_run_retrieval_with_chunks_returns_success():
    from generation_client import GenerateResult
    chunks_with_score = [{"chunk_id": "1", "text": "doc", "score": 0.8}]
    with patch("subtask_retrieval.retrieval_search", new_callable=AsyncMock, return_value=chunks_with_score):
        with patch("subtask_retrieval.generation_generate", new_callable=AsyncMock, return_value=GenerateResult(success=True, output="Generated.", attempts=1)):
            from subtask_retrieval import run_retrieval
            st = Subtask(subtask_id=uuid4(), query="test", parent_message_id=uuid4(), index=0)
            result = await run_retrieval(st, trace_id="trace-1")
            assert result.kind == "success"


def test_aggregate_all_failed_retrieval_yields_fallback_and_escalate():
    from config import FALLBACK_RESPONSE
    results = [SubtaskResult.failed_retrieval(), SubtaskResult.failed_retrieval()]
    text, should_escalate = aggregate_results(results, FALLBACK_RESPONSE)
    assert should_escalate is True
    assert text == FALLBACK_RESPONSE
