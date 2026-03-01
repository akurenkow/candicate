"""Embedding cache: get/set and reuse."""

import pytest

from retrieval.embedding_cache import EmbeddingCache


def test_cache_miss_returns_none():
    c = EmbeddingCache()
    assert c.get("hello") is None


def test_cache_set_then_get_reuses():
    c = EmbeddingCache()
    vec = [0.1, 0.2, 0.3]
    c.set("hello", vec)
    assert c.get("hello") == vec


def test_cache_reuse_same_text():
    c = EmbeddingCache()
    c.set("q1", [1.0, 2.0])
    assert c.get("q1") == [1.0, 2.0]
    c.set("q1", [3.0, 4.0])
    assert c.get("q1") == [3.0, 4.0]


def test_get_many_cached_and_missing():
    c = EmbeddingCache()
    c.set("a", [1.0])
    c.set("c", [3.0])
    vectors, missing = c.get_many(["a", "b", "c"])
    assert vectors[0] == [1.0]
    assert vectors[1] is None
    assert vectors[2] == [3.0]
    assert missing == [1]


def test_set_many_then_get_reuse():
    c = EmbeddingCache()
    c.set_many(["x", "y"], [[1.0], [2.0]])
    assert c.get("x") == [1.0]
    assert c.get("y") == [2.0]
