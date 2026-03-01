"""Output validation: relevance, prohibited content, prompt leakage, verbosity. Mockable."""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

MAX_PARAGRAPHS = 2
SMALL_PARAGRAPH_CHARS = 450
SEMANTIC_SIMILARITY_THRESHOLD = 0.45
MIN_WORDS_FOR_SIMILARITY = 6


class ValidationFailure(Enum):
    RELEVANCE = "relevance"
    PROHIBITED = "prohibited"
    PROMPT_LEAKAGE = "prompt_leakage"
    VERBOSITY = "verbosity"


@dataclass
class ValidationResult:
    valid: bool
    failure: ValidationFailure | None = None
    reason: str = ""


class OutputValidator(Protocol):
    """Validates LLM output. Implement or mock in tests."""

    def validate(
        self,
        output: str,
        prompt: str,
        context_query: str | None = None,
        validate_verbosity: bool = True,
    ) -> ValidationResult:
        ...


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def _normalize_words(text: str) -> set[str]:
    normalized = re.sub(r"[^\w\s]", " ", (text or "").lower())
    return {w for w in normalized.split() if len(w) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return max(inter / len(a | b), inter / min(len(a), len(b)) if inter else 0)


def _has_semantic_repetition(paragraphs: list[str]) -> bool:
    for i in range(len(paragraphs)):
        for j in range(i + 1, len(paragraphs)):
            wa, wb = _normalize_words(paragraphs[i]), _normalize_words(paragraphs[j])
            if len(wa) >= MIN_WORDS_FOR_SIMILARITY or len(wb) >= MIN_WORDS_FOR_SIMILARITY:
                if _jaccard(wa, wb) >= SEMANTIC_SIMILARITY_THRESHOLD:
                    return True
    return False


def _exceeds_paragraph_limit(paragraphs: list[str]) -> bool:
    if len(paragraphs) > MAX_PARAGRAPHS:
        return True
    if len(paragraphs) == 2:
        if any(len(p) > SMALL_PARAGRAPH_CHARS for p in paragraphs):
            return True
    return False


class DefaultOutputValidator:
    """Checks relevance, prohibited patterns, prompt echo, verbosity (paragraph limit, semantic repetition)."""

    def __init__(
        self,
        prohibited_substrings: list[str] | None = None,
        min_output_length: int = 1,
    ):
        self._prohibited = prohibited_substrings or [
            "ignore previous",
            "ignore all",
            "disregard instructions",
        ]
        self._min_length = min_output_length

    def validate(
        self,
        output: str,
        prompt: str,
        context_query: str | None = None,
        validate_verbosity: bool = True,
    ) -> ValidationResult:
        out = (output or "").strip()
        if len(out) < self._min_length:
            return ValidationResult(
                valid=False,
                failure=ValidationFailure.RELEVANCE,
                reason="output_too_short",
            )
        out_lower = out.lower()
        for p in self._prohibited:
            if p in out_lower:
                return ValidationResult(
                    valid=False,
                    failure=ValidationFailure.PROHIBITED,
                    reason=f"prohibited:{p}",
                )
        prompt_snippet = (prompt or "")[:200].strip()
        if prompt_snippet and prompt_snippet.lower() in out_lower:
            return ValidationResult(
                valid=False,
                failure=ValidationFailure.PROMPT_LEAKAGE,
                reason="prompt_echo",
            )
        if validate_verbosity:
            paragraphs = _split_paragraphs(out)
            if _exceeds_paragraph_limit(paragraphs):
                return ValidationResult(
                    valid=False,
                    failure=ValidationFailure.VERBOSITY,
                    reason="verbosity:too_many_paragraphs",
                )
            if _has_semantic_repetition(paragraphs):
                return ValidationResult(
                    valid=False,
                    failure=ValidationFailure.VERBOSITY,
                    reason="verbosity:semantic_repetition",
                )
        return ValidationResult(valid=True)
