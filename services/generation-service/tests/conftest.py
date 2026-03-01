import sys
from pathlib import Path

# Ensure service root is on path when running tests from project root or service dir
_svc = Path(__file__).resolve().parent.parent
if str(_svc) not in sys.path:
    sys.path.insert(0, str(_svc))

import pytest
from adapters import LLMAdapter, GenerateParams
from validation import OutputValidator, ValidationResult, ValidationFailure
from retry_controller import RetryController, RetryState
from pipeline import GenerationPipeline


class MockLLMAdapter:
    """Returns fixed or callable-defined output. Fully mockable."""

    def __init__(self, output: str | None = None, side_effect: callable = None):
        self._output = output
        self._side_effect = side_effect
        self.calls: list[GenerateParams] = []

    def generate(self, params: GenerateParams) -> str:
        self.calls.append(params)
        if self._side_effect:
            return self._side_effect(params)
        return self._output or ""


class MockValidator:
    """Configurable pass/fail for tests."""

    def __init__(self, valid: bool = True, failure: ValidationFailure | None = None, reason: str = ""):
        self._valid = valid
        self._failure = failure
        self._reason = reason
        self.calls: list[tuple[str, str, str | None]] = []

    def validate(
        self,
        output: str,
        prompt: str,
        context_query: str | None = None,
        validate_verbosity: bool = True,
    ) -> ValidationResult:
        self.calls.append((output, prompt, context_query))
        return ValidationResult(
            valid=self._valid,
            failure=self._failure,
            reason=self._reason,
        )


@pytest.fixture
def mock_llm():
    return MockLLMAdapter(output="Valid response.")


@pytest.fixture
def mock_validator_pass():
    return MockValidator(valid=True)


@pytest.fixture
def mock_validator_fail():
    return MockValidator(valid=False, failure=ValidationFailure.RELEVANCE, reason="output_too_short")


@pytest.fixture
def retry_controller():
    return RetryController(max_retries=3)


@pytest.fixture
def pipeline(mock_llm, mock_validator_pass, retry_controller):
    return GenerationPipeline(
        llm=mock_llm,
        validator=mock_validator_pass,
        retry_controller=retry_controller,
        default_temperature=0.7,
    )
