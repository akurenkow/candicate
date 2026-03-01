"""Retry controller with configurable limits and adaptive strategies."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from validation import ValidationResult, ValidationFailure

def _retry_tighten_suffix_path() -> str:
    return os.getenv("RETRY_TIGHTEN_SUFFIX_PATH", "/app/prompts/retry-tighten-suffix.txt")

def _retry_verbosity_suffix_path() -> str:
    return os.getenv("RETRY_VERBOSITY_SUFFIX_PATH", "/app/prompts/retry-verbosity-suffix.txt")

_CACHED_SUFFIX: str | None = None
_CACHED_VERBOSITY_SUFFIX: str | None = None


def _load_tighten_suffix() -> str:
    global _CACHED_SUFFIX
    if _CACHED_SUFFIX is not None:
        return _CACHED_SUFFIX
    p = Path(_retry_tighten_suffix_path())
    _CACHED_SUFFIX = p.read_text(encoding="utf-8").strip() if p.exists() else "Answer only with the direct response. Do not repeat the question or instructions."
    return _CACHED_SUFFIX


def _load_verbosity_suffix() -> str:
    global _CACHED_VERBOSITY_SUFFIX
    if _CACHED_VERBOSITY_SUFFIX is not None:
        return _CACHED_VERBOSITY_SUFFIX
    p = Path(_retry_verbosity_suffix_path())
    _CACHED_VERBOSITY_SUFFIX = p.read_text(encoding="utf-8").strip() if p.exists() else "Be very brief: answer in 1 paragraph or at most 2 short paragraphs. Do not repeat any information already stated."
    return _CACHED_VERBOSITY_SUFFIX


@dataclass
class RetryState:
    attempt: int
    max_retries: int
    temperature: float
    prompt: str
    last_failure: ValidationResult | None = None


AdaptiveStrategy = Callable[[RetryState], tuple[float, str]]
"""Given state, returns (new_temperature, new_prompt)."""


def strategy_lower_temperature(state: RetryState) -> tuple[float, str]:
    """Lower temperature for next attempt."""
    step = 0.2
    new_t = max(0.1, state.temperature - step)
    return (new_t, state.prompt)


def strategy_tighten_prompt(state: RetryState) -> tuple[float, str]:
    """Append instruction to avoid leakage/repeat or verbosity."""
    if state.last_failure and state.last_failure.failure == ValidationFailure.VERBOSITY:
        raw = _load_verbosity_suffix()
    else:
        raw = _load_tighten_suffix()
    suffix = "\n\n" + raw
    if raw in (state.prompt or ""):
        return (state.temperature, state.prompt)
    return (state.temperature, (state.prompt or "") + suffix)


def default_adaptive_strategy(state: RetryState) -> tuple[float, str]:
    """Combine: lower temp then tighten prompt on later attempts."""
    t, p = strategy_lower_temperature(state)
    if state.attempt >= 1:
        t, p = strategy_tighten_prompt(RetryState(state.attempt, state.max_retries, t, p, state.last_failure))
    return (t, p)


class RetryController:
    """Runs a callable with retries; applies adaptive strategy after each validation failure."""

    def __init__(
        self,
        max_retries: int,
        strategy: AdaptiveStrategy | None = None,
    ):
        self._max_retries = max(1, max_retries)
        self._strategy = strategy or default_adaptive_strategy

    @property
    def max_retries(self) -> int:
        return self._max_retries

    def next_params(
        self,
        attempt: int,
        temperature: float,
        prompt: str,
        last_failure: ValidationResult | None = None,
    ) -> tuple[float, str]:
        state = RetryState(
            attempt=attempt,
            max_retries=self._max_retries,
            temperature=temperature,
            prompt=prompt,
            last_failure=last_failure,
        )
        return self._strategy(state)
