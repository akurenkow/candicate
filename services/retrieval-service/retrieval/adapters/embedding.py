"""Embedding adapter interface (CTR-11/15). Mock and HTTP implementations."""

import logging
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)


class EmbeddingAdapter(Protocol):
    """Interface for obtaining text embeddings."""

    async def embed(self, text: str) -> list[float]:
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        ...


class HttpEmbeddingAdapter:
    """Calls embedding-service POST /embed. Used when Qdrant is wired."""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def embed(self, text: str) -> list[float]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.post(
                    f"{self._base_url}/embed",
                    json={"text": text},
                )
                r.raise_for_status()
                return r.json().get("vector") or []
        except Exception as e:
            logger.warning("Embedding service embed failed: %s", e)
            return []

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]


class MockEmbeddingGenerator:
    """Deterministic mock: same text -> same vector; different text -> different vector; dimension 4 for tests."""

    DIM = 4

    async def embed(self, text: str) -> list[float]:
        # Use full string so different texts yield different vectors (hash("a") % 100 can equal hash("b") % 100)
        h = sum((i + 1) * ord(c) for i, c in enumerate(text or " ")) % (10 ** 8)
        return [float((h + i) % 100) / 100.0 for i in range(self.DIM)]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]
