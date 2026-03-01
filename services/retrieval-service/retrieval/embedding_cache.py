"""In-memory cache for embeddings. Key by text; optional TTL not used in minimal."""

import hashlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EmbeddingCache:
    """Thread-safe in-memory cache: text -> vector."""

    def __init__(self, max_size: int = 10_000) -> None:
        self._store: dict[str, list[float]] = {}
        self._max_size = max_size

    def get(self, text: str) -> Optional[list[float]]:
        k = _key(text)
        return self._store.get(k)

    def set(self, text: str, vector: list[float]) -> None:
        if len(self._store) >= self._max_size:
            first_key = next(iter(self._store))
            del self._store[first_key]
        self._store[_key(text)] = vector

    def get_many(self, texts: list[str]) -> tuple[list[Optional[list[float]]], list[int]]:
        """Return (cached_vectors, indices_missing). cached_vectors[i] is None if miss."""
        result: list[Optional[list[float]]] = []
        missing: list[int] = []
        for i, t in enumerate(texts):
            v = self.get(t)
            result.append(v)
            if v is None:
                missing.append(i)
        return result, missing

    def set_many(self, texts: list[str], vectors: list[list[float]]) -> None:
        for t, v in zip(texts, vectors):
            self.set(t, v)
