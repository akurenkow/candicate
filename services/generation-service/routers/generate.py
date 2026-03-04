"""REST generate endpoint using pipeline. Pipeline is injectable for tests."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from config import (
    DEFAULT_TEMPERATURE,
    EFFECTIVE_MAX_TOKENS,
    MAX_RETRIES,
    MIN_TEMPERATURE,
    MAX_TEMPERATURE,
)
from adapters import ProviderLLMAdapter, GenerateParams
from validation import DefaultOutputValidator
from retry_controller import RetryController
from pipeline import GenerationPipeline

router = APIRouter(prefix="/generate", tags=["generate"])


class GenerateRequest(BaseModel):
    prompt: str
    context_query: str | None = None
    system_prompt: str | None = None
    model: str | None = None
    validate_verbosity: bool = False


class GenerateResponse(BaseModel):
    success: bool
    output: str
    attempts: int
    failure_reason: str | None = None


def _get_pipeline() -> GenerationPipeline:
    llm = ProviderLLMAdapter(max_tokens=EFFECTIVE_MAX_TOKENS)
    validator = DefaultOutputValidator()
    retry = RetryController(max_retries=MAX_RETRIES)
    return GenerationPipeline(
        llm=llm,
        validator=validator,
        retry_controller=retry,
        default_temperature=DEFAULT_TEMPERATURE,
        min_temperature=MIN_TEMPERATURE,
        max_temperature=MAX_TEMPERATURE,
    )


@router.post("", response_model=GenerateResponse)
def generate(
    body: GenerateRequest,
    pipeline: GenerationPipeline = Depends(_get_pipeline),
) -> GenerateResponse:
    result = pipeline.run(
        prompt=body.prompt,
        context_query=body.context_query,
        system_prompt=body.system_prompt,
        model=body.model,
        validate_verbosity=body.validate_verbosity,
    )
    return GenerateResponse(
        success=result.success,
        output=result.output,
        attempts=result.attempts,
        failure_reason=(
            result.last_validation.reason
            if result.last_validation and not result.success
            else None
        ),
    )
