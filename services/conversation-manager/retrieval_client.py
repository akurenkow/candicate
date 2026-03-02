"""HTTP client for Retrieval Service search (CTR-06)."""

import logging
from typing import Any

import httpx

from config import RETRIEVAL_SERVICE_URL, RETRIEVAL_TOP_K
from shared.tracing import OP_HTTP_OUT, log_trace

logger = logging.getLogger(__name__)
SERVICE_NAME = "conversation-manager"


async def search(
    query: str,
    trace_id: str = "",
    top_k: int | None = None,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Call POST /search. Returns list of chunk dicts (chunk_id, text, score, metadata).
    filters e.g. {"owner_id": "..."} scopes to candidate. On error or empty returns [].
    """
    k = top_k if top_k is not None else RETRIEVAL_TOP_K
    payload: dict[str, Any] = {"query": query, "top_k": k}
    if filters:
        payload["filters"] = filters
    logger.info(
        "retrieval_search outbound query=%r top_k=%s filters=%s",
        (query[:80] if query else ""),
        k,
        filters,
    )
    log_trace(
        SERVICE_NAME,
        trace_id,
        OP_HTTP_OUT,
        "retrieval-service",
        target_resource="/search",
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{RETRIEVAL_SERVICE_URL.rstrip('/')}/search",
                json=payload,
                headers={"X-Request-ID": trace_id} if trace_id else {},
            )
            r.raise_for_status()
            data: dict[str, Any] = r.json()
            return data.get("chunks") or []
    except Exception as e:
        logger.warning("Retrieval search failed: %s", e)
        return []


async def get_owner_companies(
    owner_id: str,
    trace_id: str = "",
) -> list[str]:
    """
    Return distinct company_name values for the owner (for fuzzy filter resolution).
    On error returns [].
    """
    if not (owner_id or "").strip():
        return []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{RETRIEVAL_SERVICE_URL.rstrip('/')}/owners/{owner_id.strip()}/companies",
                headers={"X-Request-ID": trace_id} if trace_id else {},
            )
            r.raise_for_status()
            data: dict[str, Any] = r.json()
            return data.get("companies") or []
    except Exception as e:
        logger.warning("Retrieval get_owner_companies failed: %s", e)
        return []
