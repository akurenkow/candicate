"""
HTTP client for Embedding Service embed_batch (CTR-17).
Runs up to MAX_THREAD_WORKERS batch requests in parallel.
Retries on 502 (transient provider errors).
"""

import asyncio
import logging
from typing import Any

import httpx

from config import EMBEDDING_SERVICE_URL, MAX_THREAD_WORKERS
from shared.tracing import OP_HTTP_OUT, log_trace

logger = logging.getLogger(__name__)
BATCH_SIZE = 32
SERVICE_NAME = "ingestion-service"
EMBED_BATCH_RETRIES = 3
EMBED_BATCH_RETRY_DELAY = 2.0


async def _embed_one_batch(
    batch: list[str],
    client: httpx.AsyncClient,
    trace_id: str,
    semaphore: asyncio.Semaphore,
) -> list[list[float]]:
    async with semaphore:
        last_err: Exception | None = None
        for attempt in range(EMBED_BATCH_RETRIES):
            try:
                r = await client.post(
                    f"{EMBEDDING_SERVICE_URL.rstrip('/')}/embed_batch",
                    json={"texts": batch},
                    timeout=120.0,
                    headers={"X-Request-ID": trace_id} if trace_id else {},
                )
                r.raise_for_status()
                data: dict[str, Any] = r.json()
                return data["vectors"]
            except httpx.HTTPStatusError as e:
                last_err = e
                if e.response.status_code == 502 and attempt < EMBED_BATCH_RETRIES - 1:
                    delay = EMBED_BATCH_RETRY_DELAY * (attempt + 1)
                    logger.warning(
                        "embed_batch 502 (attempt %s/%s), retry in %.1fs: %s",
                        attempt + 1, EMBED_BATCH_RETRIES, delay, e,
                    )
                    await asyncio.sleep(delay)
                else:
                    raise
            except Exception as e:
                last_err = e
                if attempt < EMBED_BATCH_RETRIES - 1:
                    delay = EMBED_BATCH_RETRY_DELAY * (attempt + 1)
                    logger.warning(
                        "embed_batch error (attempt %s/%s), retry in %.1fs: %s",
                        attempt + 1, EMBED_BATCH_RETRIES, delay, e,
                    )
                    await asyncio.sleep(delay)
                else:
                    raise
        if last_err:
            raise last_err
        return []


async def embed_batch(
    texts: list[str],
    client: httpx.AsyncClient,
    trace_id: str = "",
) -> list[list[float]]:
    """
    Call POST /embed_batch. Splits into batches of BATCH_SIZE and runs up to MAX_THREAD_WORKERS in parallel.
    Returns concatenated list of vectors in order.
    """
    if not texts:
        return []
    batch_count = (len(texts) + BATCH_SIZE - 1) // BATCH_SIZE
    log_trace(
        SERVICE_NAME,
        trace_id,
        OP_HTTP_OUT,
        "embedding-service",
        target_resource="/embed_batch",
        extra={"batch_count": batch_count, "max_concurrent": MAX_THREAD_WORKERS},
    )
    semaphore = asyncio.Semaphore(MAX_THREAD_WORKERS)
    batches = [texts[i : i + BATCH_SIZE] for i in range(0, len(texts), BATCH_SIZE)]
    results = await asyncio.gather(
        *[
            _embed_one_batch(batch, client, trace_id, semaphore)
            for batch in batches
        ]
    )
    all_vectors: list[list[float]] = []
    for vecs in results:
        all_vectors.extend(vecs)
    return all_vectors
