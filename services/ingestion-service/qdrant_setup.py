"""
Qdrant client and collection setup for ingestion (CTR-18).
Ensures collection exists with correct vector size on startup.
"""

import logging
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from config import EMBEDDING_DIMENSION, QDRANT_COLLECTION, QDRANT_URL

logger = logging.getLogger(__name__)


def get_qdrant_client() -> QdrantClient:
    """Create Qdrant client from config."""
    return QdrantClient(url=QDRANT_URL)


def _collection_vector_size(client: QdrantClient) -> Optional[int]:
    """Return vector size of the collection if it exists, else None."""
    try:
        info = client.get_collection(QDRANT_COLLECTION)
        params = info.config.params
        vc = getattr(params, "vectors", None)
        if vc is None:
            return None
        if hasattr(vc, "size"):
            return int(vc.size)
        if isinstance(vc, dict):
            for vec in vc.values():
                if hasattr(vec, "size"):
                    return int(vec.size)
                break
        return None
    except Exception:
        return None


def ensure_collection(client: QdrantClient) -> None:
    """
    Create collection if it does not exist, or recreate if dimension mismatch
    (e.g. after switching embedding model from 768 to 1024).
    """
    collections = client.get_collections().collections
    names = {c.name for c in collections}
    if QDRANT_COLLECTION in names:
        current_size = _collection_vector_size(client)
        if current_size is not None and current_size != EMBEDDING_DIMENSION:
            logger.warning(
                "Qdrant collection %s has dimension %s, config expects %s; recreating collection",
                QDRANT_COLLECTION,
                current_size,
                EMBEDDING_DIMENSION,
            )
            client.delete_collection(QDRANT_COLLECTION)
        else:
            logger.info("Qdrant collection %s already exists", QDRANT_COLLECTION)
            return
    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=EMBEDDING_DIMENSION, distance=Distance.COSINE),
    )
    logger.info("Created Qdrant collection %s with dimension %s", QDRANT_COLLECTION, EMBEDDING_DIMENSION)
