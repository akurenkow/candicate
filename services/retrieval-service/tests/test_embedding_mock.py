"""Mock embedding generator: deterministic, reusable."""

import pytest

from retrieval.adapters.embedding import MockEmbeddingGenerator


@pytest.mark.asyncio
async def test_embed_same_text_same_vector():
    gen = MockEmbeddingGenerator()
    v1 = await gen.embed("hello")
    v2 = await gen.embed("hello")
    assert v1 == v2
    assert len(v1) == gen.DIM


@pytest.mark.asyncio
async def test_embed_different_text_different_vector():
    gen = MockEmbeddingGenerator()
    v1 = await gen.embed("a")
    v2 = await gen.embed("b")
    assert v1 != v2


@pytest.mark.asyncio
async def test_embed_batch_order():
    gen = MockEmbeddingGenerator()
    vecs = await gen.embed_batch(["first", "second"])
    assert len(vecs) == 2
    assert vecs[0] == await gen.embed("first")
    assert vecs[1] == await gen.embed("second")
