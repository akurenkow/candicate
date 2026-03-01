"""Search API: POST /search (CTR-10)."""

import logging
import os
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from retrieval.dto import DocChunk
from retrieval.embedding_cache import EmbeddingCache
from retrieval.search_flow import retrieve
from retrieval.vector_search import StubVectorSearch, QdrantVectorSearch
from shared.tracing import OP_HTTP_IN, log_trace

logger = logging.getLogger(__name__)
SERVICE_NAME = os.getenv("SERVICE_NAME", "retrieval-service")

router = APIRouter(prefix="", tags=["search"])


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=10, ge=1, le=100)
    filters: dict | None = None


class SearchResponse(BaseModel):
    chunks: list[DocChunk]


def _get_embedding_adapter():
    from config import EMBEDDING_SERVICE_URL, QDRANT_URL
    from retrieval.adapters.embedding import HttpEmbeddingAdapter, MockEmbeddingGenerator
    if QDRANT_URL and QDRANT_URL.strip():
        return HttpEmbeddingAdapter(base_url=EMBEDDING_SERVICE_URL)
    return MockEmbeddingGenerator()  # noqa: PLC0415


def _get_vector_search():
    from config import QDRANT_COLLECTION, QDRANT_URL
    if QDRANT_URL and QDRANT_URL.strip():
        return QdrantVectorSearch(url=QDRANT_URL.strip(), collection_name=QDRANT_COLLECTION or "documents")
    return StubVectorSearch()


@router.post("/search", response_model=SearchResponse)
async def search(body: SearchRequest, request: Request) -> dict[str, Any]:
    """search(query, filters, top_k) -> list of chunks. Contract CTR-10."""
    trace_id = getattr(request.state, "request_id", "") or ""
    log_trace(SERVICE_NAME, trace_id, OP_HTTP_IN, "retrieval-service", target_resource="/search")
    logger.info(
        "POST /search request_id=%s query=%r top_k=%s filters=%s",
        trace_id or "-",
        body.query[:80] if body.query else "",
        body.top_k,
        body.filters,
    )
    adapter = _get_embedding_adapter()
    vs = _get_vector_search()
    logger.info("retrieval backend: %s", type(vs).__name__)
    cache = getattr(request.app.state, "embedding_cache", None) or EmbeddingCache()
    chunks = await retrieve(
        body.query,
        embedding_adapter=adapter,
        vector_search=vs,
        cache=cache,
        top_k=body.top_k,
        filter_metadata=body.filters,
    )
    logger.info("POST /search response request_id=%s chunks_count=%s", trace_id or "-", len(chunks))
    return {"chunks": chunks}
