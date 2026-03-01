"""HTTP client for Generation Service (CTR-07)."""

import logging
from dataclasses import dataclass

import httpx

from config import GENERATION_SERVICE_URL
from shared.tracing import OP_HTTP_OUT, log_trace

logger = logging.getLogger(__name__)
SERVICE_NAME = "conversation-manager"


@dataclass
class GenerateResult:
    success: bool
    output: str
    attempts: int = 0
    error: str | None = None


async def generate(
    prompt: str,
    context_query: str | None = None,
    system_prompt: str | None = None,
    validate_verbosity: bool = False,
    trace_id: str = "",
) -> GenerateResult:
    """Call POST /generate. Returns result with output on success."""
    log_trace(SERVICE_NAME, trace_id, OP_HTTP_OUT, "generation-service", target_resource="/generate")
    payload = {"prompt": prompt, "context_query": context_query, "validate_verbosity": validate_verbosity}
    if system_prompt is not None:
        payload["system_prompt"] = system_prompt
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{GENERATION_SERVICE_URL.rstrip('/')}/generate",
                json=payload,
                headers={"X-Request-ID": trace_id} if trace_id else {},
            )
            r.raise_for_status()
            data = r.json()
            return GenerateResult(
                success=data.get("success", False),
                output=(data.get("output") or "").strip(),
                attempts=data.get("attempts", 0),
                error=data.get("failure_reason"),
            )
    except Exception as e:
        logger.warning("Generation failed: %s", e)
        return GenerateResult(success=False, output="", error=str(e))


async def generate_clarification(
    original_query: str,
    documents: list[dict],
    history: list[dict],
    trace_id: str = "",
) -> GenerateResult:
    """Call POST /clarify. Returns clarification question for ambiguous retrieval (CTR-07)."""
    log_trace(SERVICE_NAME, trace_id, OP_HTTP_OUT, "generation-service", target_resource="/clarify")
    payload = {
        "original_query": original_query,
        "documents": documents,
        "history": history or [],
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{GENERATION_SERVICE_URL.rstrip('/')}/clarify",
                json=payload,
                headers={"X-Request-ID": trace_id} if trace_id else {},
            )
            r.raise_for_status()
            data = r.json()
            return GenerateResult(
                success=data.get("success", False),
                output=(data.get("output") or "").strip(),
                attempts=data.get("attempts", 0),
                error=data.get("failure_reason"),
            )
    except Exception as e:
        logger.warning("Clarification generation failed: %s", e)
        return GenerateResult(success=False, output="", error=str(e))
