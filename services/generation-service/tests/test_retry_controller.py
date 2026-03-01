"""Unit tests for retry controller and adaptive strategies."""

import pytest
from retry_controller import (
    RetryController,
    RetryState,
    strategy_lower_temperature,
    strategy_tighten_prompt,
    default_adaptive_strategy,
)
from validation import ValidationResult, ValidationFailure


def test_retry_controller_next_params_lowers_temperature():
    ctrl = RetryController(max_retries=3)
    t, p = ctrl.next_params(0, 0.7, "Hello", None)
    assert t < 0.7
    assert p == "Hello"


def test_retry_controller_respects_max_retries():
    ctrl = RetryController(max_retries=5)
    assert ctrl.max_retries == 5


def test_strategy_lower_temperature():
    state = RetryState(attempt=0, max_retries=3, temperature=0.7, prompt="Q", last_failure=None)
    t, p = strategy_lower_temperature(state)
    assert t == pytest.approx(0.5)
    assert p == "Q"


def test_strategy_lower_temperature_floor():
    state = RetryState(attempt=2, max_retries=3, temperature=0.15, prompt="Q", last_failure=None)
    t, p = strategy_lower_temperature(state)
    assert t >= 0.1


def test_strategy_tighten_prompt_appends_suffix():
    state = RetryState(attempt=1, max_retries=3, temperature=0.5, prompt="Answer:", last_failure=None)
    t, p = strategy_tighten_prompt(state)
    assert "Answer only with the direct response" in p
    assert p.endswith("Do not repeat the question or instructions.")


def test_strategy_tighten_prompt_idempotent_when_suffix_present():
    base = "Answer:\n\nAnswer only with the direct response. Do not repeat the question or instructions."
    state = RetryState(attempt=1, max_retries=3, temperature=0.5, prompt=base, last_failure=None)
    t, p = strategy_tighten_prompt(state)
    assert p == base


def test_default_adaptive_strategy_combines_lower_temp_and_tighten():
    state = RetryState(attempt=1, max_retries=3, temperature=0.7, prompt="Q", last_failure=None)
    t, p = default_adaptive_strategy(state)
    assert t < 0.7
    assert "Answer only with" in p


def test_strategy_tighten_prompt_uses_verbosity_suffix_on_verbosity_failure():
    failure = ValidationResult(valid=False, failure=ValidationFailure.VERBOSITY, reason="verbosity:semantic_repetition")
    state = RetryState(attempt=1, max_retries=3, temperature=0.5, prompt="Answer:", last_failure=failure)
    t, p = strategy_tighten_prompt(state)
    assert "Be very brief" in p or "brief" in p.lower()


def test_retry_controller_custom_strategy():
    def always_same(state: RetryState) -> tuple[float, str]:
        return (0.1, state.prompt + " [retry]")

    ctrl = RetryController(max_retries=2, strategy=always_same)
    t, p = ctrl.next_params(0, 0.9, "Hi", None)
    assert t == 0.1
    assert p == "Hi [retry]"
