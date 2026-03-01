"""SubtaskResult and aggregate behavior for failure states."""

import sys
from pathlib import Path

_path = Path(__file__).resolve().parent.parent
if str(_path) not in sys.path:
    sys.path.insert(0, str(_path))

from config import FALLBACK_RESPONSE
from subtask_result import (
    SubtaskResult,
    aggregate_results,
    FAILED_RETRIEVAL,
    FAILED_GENERATION,
    PARTIAL_FAILURE_ANNOTATION,
)


def test_aggregate_empty():
    text, should_escalate = aggregate_results([], FALLBACK_RESPONSE)
    assert text == ""
    assert should_escalate is False


def test_aggregate_all_failed_retrieval():
    results = [SubtaskResult.failed_retrieval(), SubtaskResult.failed_retrieval()]
    text, should_escalate = aggregate_results(results, FALLBACK_RESPONSE)
    assert text == FALLBACK_RESPONSE
    assert should_escalate is True


def test_aggregate_all_failed_generation_escalate():
    results = [SubtaskResult.failed_generation(), SubtaskResult.failed_generation()]
    text, should_escalate = aggregate_results(results, FALLBACK_RESPONSE)
    assert text == FALLBACK_RESPONSE
    assert should_escalate is True


def test_aggregate_mixed_success_and_failed_annotates_partial():
    results = [
        SubtaskResult.failed_retrieval(),
        SubtaskResult.success("answer one"),
        SubtaskResult.success("answer two"),
    ]
    text, should_escalate = aggregate_results(results, FALLBACK_RESPONSE)
    assert "answer one" in text and "answer two" in text
    assert PARTIAL_FAILURE_ANNOTATION in text
    assert should_escalate is False


def test_aggregate_mixed_success_and_failed_generation_annotates():
    results = [
        SubtaskResult.failed_generation(),
        SubtaskResult.success("ok"),
    ]
    text, should_escalate = aggregate_results(results, FALLBACK_RESPONSE)
    assert "ok" in text
    assert PARTIAL_FAILURE_ANNOTATION in text
    assert should_escalate is False


def test_aggregate_all_success():
    results = [SubtaskResult.success("a"), SubtaskResult.success("b")]
    text, should_escalate = aggregate_results(results, FALLBACK_RESPONSE)
    assert text == "a\n\nb"
    assert should_escalate is False
