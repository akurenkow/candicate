"""Generation pipeline: generate -> validate -> retry with adaptive strategy. Fully mockable."""

from dataclasses import dataclass
from typing import Protocol

from adapters import GenerateParams, LLMAdapter
from retry_controller import RetryController
from validation import OutputValidator, ValidationResult


@dataclass
class PipelineResult:
    success: bool
    output: str
    attempts: int
    last_validation: ValidationResult | None = None


class GenerationPipeline:
    """Runs LLM generation with validation and retries. Injects adapter and validator."""

    def __init__(
        self,
        llm: LLMAdapter,
        validator: OutputValidator,
        retry_controller: RetryController,
        default_temperature: float = 0.7,
        min_temperature: float = 0.1,
        max_temperature: float = 1.0,
    ):
        self._llm = llm
        self._validator = validator
        self._retry = retry_controller
        self._default_temperature = default_temperature
        self._min_t = min_temperature
        self._max_t = max_temperature

    def run(
        self,
        prompt: str,
        context_query: str | None = None,
        system_prompt: str | None = None,
        model: str | None = None,
        validate_verbosity: bool = False,
        max_tokens: int | None = None,
    ) -> PipelineResult:
        temperature = self._default_temperature
        current_prompt = prompt
        last_validation: ValidationResult | None = None
        for attempt in range(self._retry.max_retries):
            params = GenerateParams(
                prompt=current_prompt,
                temperature=max(self._min_t, min(self._max_t, temperature)),
                model=model,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
            )
            output = self._llm.generate(params)
            last_validation = self._validator.validate(
                output, current_prompt, context_query, validate_verbosity=validate_verbosity
            )
            if last_validation.valid:
                return PipelineResult(
                    success=True,
                    output=output,
                    attempts=attempt + 1,
                    last_validation=last_validation,
                )
            temperature, current_prompt = self._retry.next_params(
                attempt, temperature, current_prompt, last_validation
            )
        return PipelineResult(
            success=False,
            output="",
            attempts=self._retry.max_retries,
            last_validation=last_validation,
        )
