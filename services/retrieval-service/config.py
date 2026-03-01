"""Retrieval service configuration. TOP_K used when search is implemented."""

import os


def get_env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


# TOP_K: number of chunks to return per search; prefer TOP_K, fallback to DEFAULT_TOP_K
TOP_K = int(os.getenv("TOP_K") or os.getenv("DEFAULT_TOP_K", "10"))
QDRANT_URL = get_env("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = get_env("QDRANT_COLLECTION", "documents")
EMBEDDING_SERVICE_URL = get_env("EMBEDDING_SERVICE_URL", "http://embedding-service:8000")
