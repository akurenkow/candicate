"""Orchestrates embedding (with cache) and vector search. Isolated for testing."""

import logging
from typing import Any

from .adapters.embedding import EmbeddingAdapter
from .dto import DocChunk
from .embedding_cache import EmbeddingCache
from .vector_search import VectorSearch

logger = logging.getLogger(__name__)


async def retrieve(
    query: str,
    *,
    embedding_adapter: EmbeddingAdapter,
    vector_search: VectorSearch,
    cache: EmbeddingCache | None = None,
    top_k: int = 10,
    filter_metadata: dict | None = None,
) -> list[DocChunk]:
    """
    Get embedding for query (cache first), then run vector search.
    Returns top-k chunks; empty if no documents found.
    """
    cache = cache or EmbeddingCache()
    vector: list[float] | None = cache.get(query)
    if vector is None:
        vector = await embedding_adapter.embed(query)
        cache.set(query, vector)
    if not vector or len(vector) == 0:
        logger.warning("Empty embedding for query; skipping vector search")
        return []
    chunks = await vector_search.search(vector, top_k=top_k, filter_metadata=filter_metadata)
    return chunks
