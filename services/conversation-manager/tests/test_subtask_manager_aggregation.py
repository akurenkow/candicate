"""SubtaskManager and aggregation under mixed success/failure conditions."""

import sys
from pathlib import Path
from uuid import UUID, uuid4

_path = Path(__file__).resolve().parent.parent
if str(_path) not in sys.path:
    sys.path.insert(0, str(_path))

from config import FALLBACK_RESPONSE
from query_decomposition import Subtask
from subtask_result import (
    SubtaskResult,
    aggregate_results,
    FAILED_RETRIEVAL,
    FAILED_GENERATION,
    PARTIAL_FAILURE_ANNOTATION,
)
from subtask_manager import SubtaskManager


def _subtasks(n: int, parent_id: UUID | None = None) -> list[Subtask]:
    mid = parent_id or uuid4()
    return [Subtask(subtask_id=uuid4(), query=f"q{i}", parent_message_id=mid, index=i) for i in range(n)]


def test_manager_all_failed_retrieval():
    st = _subtasks(2)
    results = [SubtaskResult.failed_retrieval(), SubtaskResult.failed_retrieval()]
    m = SubtaskManager(st, results)
    assert m.all_failed() is True
    assert m.has_partial_failures() is False
    assert m.successful_results() == []


def test_manager_all_failed_generation():
    st = _subtasks(2)
    results = [SubtaskResult.failed_generation(), SubtaskResult.failed_generation()]
    m = SubtaskManager(st, results)
    assert m.all_failed() is True
    assert m.successful_results() == []


def test_manager_mixed_success_and_failures():
    st = _subtasks(3)
    results = [
        SubtaskResult.success("a"),
        SubtaskResult.failed_retrieval(),
        SubtaskResult.failed_generation(),
    ]
    m = SubtaskManager(st, results)
    assert m.all_failed() is False
    assert m.has_partial_failures() is True
    assert len(m.successful_results()) == 1
    assert m.successful_results()[0].text == "a"


def test_manager_all_success():
    st = _subtasks(2)
    results = [SubtaskResult.success("x"), SubtaskResult.success("y")]
    m = SubtaskManager(st, results)
    assert m.all_failed() is False
    assert m.has_partial_failures() is False
    assert len(m.successful_results()) == 2


def test_aggregation_mixed_escalate_only_when_all_fail():
    r1 = [SubtaskResult.failed_retrieval(), SubtaskResult.failed_generation()]
    text, esc = aggregate_results(r1, FALLBACK_RESPONSE)
    assert esc is True
    assert text == FALLBACK_RESPONSE

    r2 = [SubtaskResult.success("only"), SubtaskResult.failed_generation()]
    text2, esc2 = aggregate_results(r2, FALLBACK_RESPONSE)
    assert esc2 is False
    assert "only" in text2
    assert PARTIAL_FAILURE_ANNOTATION in text2


def test_aggregation_partial_no_annotation_when_no_successful_text():
    results = [SubtaskResult.failed_retrieval(), SubtaskResult.success("")]
    text, esc = aggregate_results(results, FALLBACK_RESPONSE)
    assert esc is False
    assert text == ""
    assert PARTIAL_FAILURE_ANNOTATION not in text
