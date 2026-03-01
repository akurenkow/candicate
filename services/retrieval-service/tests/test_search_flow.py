"""Search flow: embedding reuse via cache, empty when no docs."""

import pytest

from retrieval.adapters.embedding import MockEmbeddingGenerator
from retrieval.dto import DocChunk
from retrieval.embedding_cache import EmbeddingCache
from retrieval.search_flow import retrieve
from retrieval.vector_search import StubVectorSearch, MockVectorSearch


@pytest.mark.asyncio
async def test_retrieve_uses_cache_second_call_reuses_embedding():
    cache = EmbeddingCache()
    adapter = MockEmbeddingGenerator()
    vs = StubVectorSearch()
    r1 = await retrieve("q", embedding_adapter=adapter, vector_search=vs, cache=cache, top_k=5)
    r2 = await retrieve("q", embedding_adapter=adapter, vector_search=vs, cache=cache, top_k=5)
    assert r1 == r2 == []
    assert cache.get("q") is not None


@pytest.mark.asyncio
async def test_retrieve_no_docs_returns_empty():
    cache = EmbeddingCache()
    adapter = MockEmbeddingGenerator()
    vs = StubVectorSearch()
    chunks = await retrieve("query", embedding_adapter=adapter, vector_search=vs, cache=cache, top_k=3)
    assert chunks == []


@pytest.mark.asyncio
async def test_retrieve_with_mock_search_returns_chunks():
    cache = EmbeddingCache()
    adapter = MockEmbeddingGenerator()
    vs = MockVectorSearch([DocChunk(chunk_id="1", text="doc", score=0.9)])
    chunks = await retrieve("query", embedding_adapter=adapter, vector_search=vs, cache=cache, top_k=5)
    assert len(chunks) == 1
    assert chunks[0].text == "doc"
