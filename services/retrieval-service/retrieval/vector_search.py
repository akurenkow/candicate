"""Vector search interface: embedding in -> top-k document chunks out."""

import asyncio
import logging
from typing import Protocol

from .dto import DocChunk

logger = logging.getLogger(__name__)


class VectorSearch(Protocol):
    """Interface for similarity search by vector."""

    async def search(
        self,
        embedding: list[float],
        top_k: int,
        filter_metadata: dict | None = None,
    ) -> list[DocChunk]:
        ...


class StubVectorSearch:
    """Returns empty list (no documents). Used when Qdrant not wired."""

    async def search(
        self,
        embedding: list[float],
        top_k: int,
        filter_metadata: dict | None = None,
    ) -> list[DocChunk]:
        return []


class MockVectorSearch:
    """Returns fixed chunks for testing. Same embedding always gets same top_k chunks."""

    def __init__(self, chunks: list[DocChunk] | None = None) -> None:
        self._chunks = chunks or []

    async def search(
        self,
        embedding: list[float],
        top_k: int,
        filter_metadata: dict | None = None,
    ) -> list[DocChunk]:
        return self._chunks[:top_k]


def _payload_to_doc_chunk(r, score: float = 0.0) -> DocChunk:
    raw = getattr(r, "payload", None) or (r.payload if hasattr(r, "payload") else None)
    payload = dict(raw) if raw is not None else {}
    text = (payload.get("text") or "").strip()
    meta = {k: v for k, v in payload.items() if k not in ("chunk_id", "text", "document_id", "owner_id")}
    if not text and meta:
        text = " ".join(str(v).strip() for k, v in sorted(meta.items()) if v is not None and str(v).strip())
    point_id = getattr(r, "id", None) or (r.id if hasattr(r, "id") else None)
    return DocChunk(
        chunk_id=str(payload.get("chunk_id", point_id)),
        text=text or "",
        score=score,
        metadata=meta,
    )


class QdrantVectorSearch:
    """Vector search against Qdrant. Uses filter_metadata (e.g. owner_id) for scoping. Merges in section_kind=general chunks so salary/expectations are retrievable."""

    GENERAL_SCROLL_LIMIT = 15

    def __init__(self, url: str, collection_name: str) -> None:
        from qdrant_client import QdrantClient
        self._client = QdrantClient(url=url)
        self._collection = collection_name

    async def search(
        self,
        embedding: list[float],
        top_k: int,
        filter_metadata: dict | None = None,
    ) -> list[DocChunk]:
        from qdrant_client.models import Filter, FieldCondition, MatchValue, SearchRequest
        if not embedding or len(embedding) == 0:
            logger.warning("QdrantVectorSearch: empty embedding, returning no chunks")
            return []
        query_filter = None
        if filter_metadata:
            must = []
            for key, value in filter_metadata.items():
                if value is None:
                    continue
                v = str(value).strip() if isinstance(value, str) else value
                if isinstance(v, str) and v == "":
                    continue
                must.append(FieldCondition(key=key, match=MatchValue(value=v)))
            if must:
                query_filter = Filter(must=must)
        loop = asyncio.get_event_loop()
        try:
            search_request = SearchRequest(
                vector=embedding,
                limit=top_k,
                filter=query_filter,
                with_payload=True,
            )
            response = await loop.run_in_executor(
                None,
                lambda: self._client.http.search_api.search_points(
                    collection_name=self._collection,
                    search_request=search_request,
                ),
            )
            results = getattr(response, "result", None)
            if results is None and isinstance(response, list):
                results = response
            results = results if results is not None else []
            vector_chunks = [_payload_to_doc_chunk(r, float(getattr(r, "score", 0) or 0.0)) for r in results]
            # Boost score for general chunks so CM passes CLARIFY/ANSWER threshold even when semantic score is low
            GENERAL_MIN_SCORE = 0.65
            boosted = []
            for c in vector_chunks:
                if (c.metadata or {}).get("section_kind") == "general" and c.score < GENERAL_MIN_SCORE:
                    boosted.append(DocChunk(chunk_id=c.chunk_id, text=c.text, score=GENERAL_MIN_SCORE, metadata=c.metadata or {}))
                else:
                    boosted.append(c)
            vector_chunks = boosted
            seen_ids = {c.chunk_id for c in vector_chunks}
        except Exception as e:
            logger.warning("QdrantVectorSearch vector search failed: %s", e)
            vector_chunks = []
            seen_ids = set()

        general_chunks: list[DocChunk] = []
        if query_filter is not None and filter_metadata:
            scroll_must = []
            for key, value in filter_metadata.items():
                if value is None:
                    continue
                v = str(value).strip() if isinstance(value, str) else value
                if isinstance(v, str) and v == "":
                    continue
                scroll_must.append(FieldCondition(key=key, match=MatchValue(value=v)))
            if scroll_must:
                try:
                    # Scroll by owner_id only; filter section_kind=general in Python so we don't rely on Qdrant payload filter
                    owner_filter = Filter(must=scroll_must)
                    scroll_result = await loop.run_in_executor(
                        None,
                        lambda sf=owner_filter: self._client.scroll(
                            collection_name=self._collection,
                            scroll_filter=sf,
                            limit=self.GENERAL_SCROLL_LIMIT * 2,
                            with_payload=True,
                            with_vectors=False,
                        ),
                    )
                    raw_records = scroll_result[0] if isinstance(scroll_result, (list, tuple)) else []
                    general_score = 0.65
                    for rec in raw_records:
                        raw = getattr(rec, "payload", None)
                        payload = dict(raw) if raw is not None else {}
                        if payload.get("section_kind") != "general":
                            continue
                        c = _payload_to_doc_chunk(rec, general_score)
                        if c.chunk_id in seen_ids:
                            continue
                        seen_ids.add(c.chunk_id)
                        general_chunks.append(c)
                except Exception as scroll_err:
                    logger.debug("QdrantVectorSearch scroll general failed (non-fatal): %s", scroll_err)

        merged = general_chunks + vector_chunks
        logger.info(
            "QdrantVectorSearch result filter_metadata=%s vector_chunks=%s general_chunks=%s total=%s",
            filter_metadata,
            len(vector_chunks),
            len(general_chunks),
            len(merged),
        )
        return merged[:top_k]
