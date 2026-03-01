"""Tracks status of all subqueries belonging to a message."""

from __future__ import annotations

from query_decomposition import Subtask
from subtask_result import SubtaskResult, SUCCESS, FAILED_RETRIEVAL, FAILED_NO_USABLE_TEXT, FAILED_GENERATION


def _is_failure(kind: str) -> bool:
    return kind in (FAILED_RETRIEVAL, FAILED_NO_USABLE_TEXT, FAILED_GENERATION)


class SubtaskManager:
    """In-memory tracker for subtask results of one message."""

    __slots__ = ("_subtasks", "_results")

    def __init__(self, subtasks: list[Subtask], results: list[SubtaskResult]) -> None:
        self._subtasks = subtasks
        self._results = results

    def all_failed(self) -> bool:
        """True if every subtask failed (retrieval or generation beyond retry)."""
        if not self._results:
            return False
        return all(_is_failure(r.kind) for r in self._results)

    def has_partial_failures(self) -> bool:
        """True if at least one subtask failed and at least one succeeded."""
        failed = sum(1 for r in self._results if _is_failure(r.kind))
        success = sum(1 for r in self._results if r.kind == SUCCESS)
        return failed > 0 and success > 0

    def successful_results(self) -> list[SubtaskResult]:
        """Ordered list of successful subtask results."""
        return [r for r in self._results if r.kind == SUCCESS and r.text]
