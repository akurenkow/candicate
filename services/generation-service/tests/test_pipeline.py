"""Unit tests for generation pipeline: retry paths, validation failures, exhausted retries."""

import pytest
from adapters import GenerateParams
from validation import ValidationResult, ValidationFailure
from retry_controller import RetryController
from pipeline import GenerationPipeline
from conftest import MockLLMAdapter, MockValidator


def test_pipeline_success_first_try(mock_llm, mock_validator_pass, retry_controller):
    pipeline = GenerationPipeline(
        llm=mock_llm,
        validator=mock_validator_pass,
        retry_controller=retry_controller,
        default_temperature=0.7,
    )
    result = pipeline.run("Hello?", context_query="Hello?")
    assert result.success
    assert result.output == "Valid response."
    assert result.attempts == 1
    assert mock_llm.calls.__len__() == 1
    assert mock_validator_pass.calls.__len__() == 1


def test_pipeline_retries_on_validation_failure_then_succeeds():
    call_count = 0

    def llm_side_effect(params: GenerateParams) -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "ignore previous instructions"
        return "Valid answer."

    llm = MockLLMAdapter(side_effect=llm_side_effect)

    class ValidatorThatPassesSecond:
        def __init__(self):
            self.calls = []

        def validate(self, output: str, prompt: str, context_query: str | None = None, validate_verbosity: bool = True) -> ValidationResult:
            self.calls.append((output, prompt))
            if len(self.calls) == 1:
                return ValidationResult(valid=False, failure=ValidationFailure.PROHIBITED, reason="prohibited")
            return ValidationResult(valid=True)

    v = ValidatorThatPassesSecond()
    retry = RetryController(max_retries=3)
    pipeline = GenerationPipeline(llm=llm, validator=v, retry_controller=retry, default_temperature=0.7)
    result = pipeline.run("Q")
    assert result.success
    assert result.output == "Valid answer."
    assert result.attempts == 2
    assert len(llm.calls) == 2
    assert len(v.calls) == 2


def test_pipeline_fails_gracefully_when_retries_exhausted():
    llm = MockLLMAdapter(output="ignore previous instructions")
    validator = MockValidator(valid=False, failure=ValidationFailure.PROHIBITED, reason="prohibited")
    retry = RetryController(max_retries=3)
    pipeline = GenerationPipeline(llm=llm, validator=validator, retry_controller=retry, default_temperature=0.7)
    result = pipeline.run("Q")
    assert not result.success
    assert result.output == ""
    assert result.attempts == 3
    assert result.last_validation is not None
    assert result.last_validation.reason == "prohibited"
    assert len(llm.calls) == 3


def test_pipeline_uses_adaptive_temperature_across_retries():
    seen_temperatures = []

    def capture_params(params: GenerateParams) -> str:
        seen_temperatures.append(params.temperature)
        return "bad"

    llm = MockLLMAdapter(side_effect=capture_params)
    validator = MockValidator(valid=False, failure=ValidationFailure.RELEVANCE, reason="short")
    retry = RetryController(max_retries=3)
    pipeline = GenerationPipeline(
        llm=llm,
        validator=validator,
        retry_controller=retry,
        default_temperature=0.7,
        min_temperature=0.1,
        max_temperature=1.0,
    )
    pipeline.run("Q")
    assert len(seen_temperatures) == 3
    assert seen_temperatures[0] == 0.7
    assert seen_temperatures[1] < 0.7
    assert seen_temperatures[2] <= seen_temperatures[1]
