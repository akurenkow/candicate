"""Result of processing a single subtask. Clear failure states for retrieval/generation."""

from __future__ import annotations

from dataclasses import dataclass

FAILED_RETRIEVAL = "failed_retrieval"
FAILED_LOW_SCORE = "failed_low_score"
FAILED_NO_USABLE_TEXT = "failed_no_usable_text"
FAILED_GENERATION = "failed_generation"
SUCCESS = "success"

PARTIAL_FAILURE_ANNOTATION = "Some parts of your question could not be answered."


def _is_failure(kind: str) -> bool:
    return kind in (FAILED_RETRIEVAL, FAILED_LOW_SCORE, FAILED_NO_USABLE_TEXT, FAILED_GENERATION)


@dataclass(frozen=True)
class SubtaskResult:
    kind: str  # SUCCESS | FAILED_RETRIEVAL | FAILED_GENERATION
    text: str = ""

    @classmethod
    def success(cls, text: str) -> SubtaskResult:
        return cls(kind=SUCCESS, text=text or "")

    @classmethod
    def failed_retrieval(cls) -> SubtaskResult:
        return cls(kind=FAILED_RETRIEVAL)

    @classmethod
    def failed_low_score(cls) -> SubtaskResult:
        return cls(kind=FAILED_LOW_SCORE)

    @classmethod
    def failed_no_usable_text(cls) -> SubtaskResult:
        return cls(kind=FAILED_NO_USABLE_TEXT)

    @classmethod
    def failed_generation(cls) -> SubtaskResult:
        return cls(kind=FAILED_GENERATION)


def aggregate_results(
    results: list[SubtaskResult],
    fallback_response: str,
    *,
    partial_failure_annotation: str | None = PARTIAL_FAILURE_ANNOTATION,
) -> tuple[str, bool]:
    """Returns (assistant_text, should_escalate). Merges successful results; annotates partial failures; escalates when all subtasks failed (retrieval or generation)."""
    if not results:
        return "", False
    all_failed = all(_is_failure(r.kind) for r in results)
    if all_failed:
        return fallback_response, True
    parts = [r.text.strip() for r in results if r.kind == SUCCESS and r.text and r.text.strip()]
    body = "\n\n".join(parts) if parts else ""
    has_partial = any(_is_failure(r.kind) for r in results) and bool(parts)
    if has_partial and partial_failure_annotation:
        body = body.rstrip() + "\n\n" + partial_failure_annotation
    return body, False
