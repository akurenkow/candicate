"""Clarification question generation (CTR-07, CTR-14)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from routers.generate import _get_pipeline
from pipeline import GenerationPipeline

router = APIRouter(prefix="/clarify", tags=["clarify"])


class ClarifyRequest(BaseModel):
    original_query: str
    documents: list[dict]
    history: list[dict]


class ClarifyResponse(BaseModel):
    success: bool
    output: str
    attempts: int = 0
    failure_reason: str | None = None


def _build_clarification_prompt(original_query: str, documents: list[dict], history: list[dict]) -> str:
    parts = [f"User question: {original_query}"]
    if documents:
        texts = [d.get("text", "")[:500] for d in documents if d.get("text")]
        if texts:
            parts.append("Retrieved context (excerpts):")
            parts.extend(f"- {t}" for t in texts[:5])
    if history:
        parts.append("Previous turns:")
        for h in history[-5:]:
            role = h.get("role", "")
            content = (h.get("content") or "")[:200]
            parts.append(f"  {role}: {content}")
    parts.append("Generate a single short clarifying question to narrow down what the user needs. Output only the question, no preamble.")
    return "\n".join(parts)


@router.post("", response_model=ClarifyResponse)
def clarify(
    body: ClarifyRequest,
    pipeline: GenerationPipeline = Depends(_get_pipeline),
) -> ClarifyResponse:
    prompt = _build_clarification_prompt(
        body.original_query,
        body.documents or [],
        body.history or [],
    )
    result = pipeline.run(
        prompt=prompt,
        context_query=body.original_query,
        system_prompt="You are a helpful assistant. Output only a short clarifying question.",
    )
    return ClarifyResponse(
        success=result.success,
        output=result.output,
        attempts=result.attempts,
        failure_reason=(
            result.last_validation.reason
            if result.last_validation and not result.success
            else None
        ),
    )
