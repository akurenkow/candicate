"""HTTP client for Embedding Service (post-process fidelity)."""

import logging
from typing import Optional

import httpx

from config import EMBEDDING_SERVICE_URL, POSTPROCESS_EMBED_TIMEOUT
from shared.tracing import OP_HTTP_OUT, log_trace

logger = logging.getLogger(__name__)
SERVICE_NAME = "conversation-manager"


async def embed_batch(
    texts: list[str],
    trace_id: str = "",
) -> Optional[list[list[float]]]:
    """POST /embed_batch with up to 2 texts. Returns vectors or None on error."""
    texts = [t.strip() for t in texts if (t or "").strip()]
    if not texts or len(texts) > 2:
        return None
    url = f"{EMBEDDING_SERVICE_URL.rstrip('/')}/embed_batch"
    log_trace(SERVICE_NAME, trace_id, OP_HTTP_OUT, "embedding-service", target_resource="/embed_batch")
    try:
        async with httpx.AsyncClient(timeout=POSTPROCESS_EMBED_TIMEOUT) as client:
            r = await client.post(
                url,
                json={"texts": texts},
                headers={"X-Request-ID": trace_id} if trace_id else {},
            )
            r.raise_for_status()
            return r.json().get("vectors")
    except Exception as e:
        logger.warning("embed_batch failed: %s", e)
        return None
