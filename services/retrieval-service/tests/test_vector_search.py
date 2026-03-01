"""Vector search: top-k and empty behavior."""

import pytest

from retrieval.dto import DocChunk
from retrieval.vector_search import StubVectorSearch, MockVectorSearch


@pytest.mark.asyncio
async def test_stub_returns_empty():
    vs = StubVectorSearch()
    chunks = await vs.search([0.1, 0.2], top_k=5)
    assert chunks == []


@pytest.mark.asyncio
async def test_mock_returns_top_k():
    c1 = DocChunk(chunk_id="1", text="one", score=0.9)
    c2 = DocChunk(chunk_id="2", text="two", score=0.8)
    c3 = DocChunk(chunk_id="3", text="three", score=0.7)
    vs = MockVectorSearch([c1, c2, c3])
    chunks = await vs.search([0.0] * 4, top_k=2)
    assert len(chunks) == 2
    assert chunks[0].chunk_id == "1"
    assert chunks[1].chunk_id == "2"


@pytest.mark.asyncio
async def test_mock_top_k_exceeds_available():
    vs = MockVectorSearch([DocChunk(chunk_id="1", text="x", score=1.0)])
    chunks = await vs.search([0.0], top_k=10)
    assert len(chunks) == 1
