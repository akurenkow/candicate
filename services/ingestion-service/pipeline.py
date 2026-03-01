"""
FLOW-02 ingestion pipeline: process upload_document command.
Transactionally: replace old version, chunk, embed, write Qdrant + PostgreSQL, publish result.
Uses MAX_THREAD_WORKERS for parallel translation and parallel embed batches.
"""

import asyncio
import base64
import json
import logging
from typing import Optional, Tuple
from uuid import uuid4

import asyncpg
from qdrant_client import QdrantClient
from qdrant_client.models import PointIdsList, PointStruct

import httpx

from chunking import chunk_text_sentence_aware
from config import (
    OLLAMA_CHUNK_MAX_SIZE,
    OLLAMA_CHUNK_OVERLAP,
    MAX_DOCUMENT_PART_CHARS,
    MAX_THREAD_WORKERS,
    MAX_TRANSLATION_BLOCK_CHARS,
    QDRANT_COLLECTION,
    RESULT_QUEUE,
)
from semantic_resume import (
    build_resume_tree,
    chunks_from_semantic_resume,
    split_resume_sections,
    split_experience_by_companies,
)
from semantic_resume.parser import MIN_DELIMITER_DASHES
from shared.tracing import OP_DB_WRITE, OP_QUEUE_PUBLISH, OP_VECTOR_WRITE, log_trace
from db import (
    create_pool,
    find_document_by_owner_external,
    delete_chunks_and_mark_superseded,
    insert_chunks,
    insert_document,
    set_document_indexed,
    set_document_failed,
    get_chunk_ids_by_document_id,
)
from embedding_client import embed_batch
from parsers import extract_text
from translation_client import translate_ru_to_en_async

logger = logging.getLogger(__name__)
SERVICE_NAME = "ingestion-service"


def _split_content_into_parts(content: str, max_part_chars: int) -> list[str]:
    """Split content into parts at newline boundaries, each part <= max_part_chars."""
    if not content or max_part_chars <= 0:
        return [content] if content else []
    parts: list[str] = []
    rest = content
    while rest:
        if len(rest) <= max_part_chars:
            parts.append(rest)
            break
        chunk = rest[:max_part_chars]
        last_nl = chunk.rfind("\n")
        if last_nl > 0:
            parts.append(rest[: last_nl + 1])
            rest = rest[last_nl + 1 :].lstrip("\n")
        else:
            parts.append(chunk)
            rest = rest[max_part_chars:]
    return parts


_EXPERIENCE_DELIMITER = "-" * MIN_DELIMITER_DASHES + "\n"


def _split_general_for_translation(text: str, max_chars: int) -> list[str]:
    """Split general section into blocks <= max_chars (at paragraph boundaries) so each translation request stays within LLM limits."""
    if not text or max_chars <= 0:
        return [text] if (text or "").strip() else []
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    blocks: list[str] = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            blocks.append(para)
            continue
        rest = para
        while rest:
            if len(rest) <= max_chars:
                blocks.append(rest)
                break
            chunk = rest[:max_chars]
            last_nl = chunk.rfind("\n")
            if last_nl > 0:
                blocks.append(rest[: last_nl + 1].strip())
                rest = rest[last_nl + 1 :].lstrip()
            else:
                blocks.append(rest[:max_chars])
                rest = rest[max_chars:].lstrip()
    return blocks if blocks else [text]


def _split_content_preserving_delimiter(content: str, max_part_chars: int) -> list[str]:
    """
    Split content at company boundaries only (L1 "- Company:"), so no part starts mid-company
    and loses metadata. Each part gets full company blocks.
    """
    general_text, experience_text = split_resume_sections(content)
    if not experience_text.strip():
        return [content] if content.strip() else []
    _sep = "\n\n-----\n\n"
    first_max = max_part_chars - len(general_text) - len(_sep)
    if first_max <= 0:
        first_max = max_part_chars // 2
    rest_max = max_part_chars - len(_EXPERIENCE_DELIMITER)
    company_blocks = split_experience_by_companies(experience_text)
    if not company_blocks:
        return [content]
    if len(experience_text) <= first_max:
        return [content]
    part_groups: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    limit = first_max
    for block in company_blocks:
        need = (2 if current else 0) + len(block)
        if current and current_len + need > limit:
            part_groups.append(current)
            current = []
            current_len = 0
            limit = rest_max
        current.append(block)
        current_len += (2 if current_len else 0) + len(block)
    if current:
        part_groups.append(current)
    parts = [general_text + _sep + "\n\n".join(part_groups[0])]
    for g in part_groups[1:]:
        parts.append(_EXPERIENCE_DELIMITER + "\n\n".join(g))
    return parts


def _chunk_one_part(part: str) -> tuple[list[str], list[dict]]:
    """Chunk a single part (sync). Returns (chunks_text, chunks_meta)."""
    resume = build_resume_tree(part)
    has_general = bool(resume.general.content_blocks)
    has_companies = bool(resume.companies)
    if has_general or has_companies:
        chunks_with_meta = chunks_from_semantic_resume(
            resume,
            include_metadata=True,
            split_technologies=True,
            max_chunk_size=OLLAMA_CHUNK_MAX_SIZE,
            chunk_overlap=OLLAMA_CHUNK_OVERLAP,
        )
        return [t for t, _ in chunks_with_meta], [m for _, m in chunks_with_meta]
    chunks_text = chunk_text_sentence_aware(
        part,
        chunk_size=OLLAMA_CHUNK_MAX_SIZE,
        overlap=OLLAMA_CHUNK_OVERLAP,
    )
    return chunks_text, [{}] * len(chunks_text)


def _parse_body(body: bytes) -> dict:
    data = json.loads(body)
    if data.get("command") != "upload_document":
        raise ValueError("Invalid command")
    job_id = data.get("job_id") or ""
    trace_id = data.get("trace_id") or ""
    owner_id = data.get("owner_id") or ""
    external_id = data.get("external_id")
    filename = data.get("filename") or ""
    content_type = data.get("content_type") or ""
    content_b64 = data.get("content_base64") or ""
    if not job_id or not owner_id or not filename or not content_b64:
        raise ValueError("Missing required fields")
    raw = base64.b64decode(content_b64, validate=True)
    return {
        "job_id": job_id,
        "trace_id": trace_id,
        "owner_id": owner_id,
        "external_id": external_id,
        "filename": filename,
        "content_type": content_type,
        "raw": raw,
    }


async def run_pipeline(
    body: bytes,
    pool: asyncpg.Pool,
    qdrant: QdrantClient,
    http_client: httpx.AsyncClient,
    publish_result_fn: callable,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Process one upload_document message.
    Returns (success, document_id_str, error_message).
    On failure rolls back DB and removes any points written to Qdrant.
    """
    payload = _parse_body(body)
    job_id = payload["job_id"]
    trace_id = payload["trace_id"]
    owner_id = (payload.get("owner_id") or "").strip()
    external_id = payload.get("external_id")
    filename = payload["filename"]
    content_type = payload["content_type"]
    raw = payload["raw"]

    try:
        raw_content = extract_text(content_type, raw)
    except Exception as e:
        logger.exception("Parse error for job %s: %s", job_id, e)
        return False, None, str(e)

    # Translate general and each company separately (up to MAX_THREAD_WORKERS concurrent tasks).
    # General section is split into blocks to avoid LLM timeout/failure on large text; companies already per-block.
    general_raw, experience_raw = split_resume_sections(raw_content)
    general_blocks = _split_general_for_translation(general_raw, MAX_TRANSLATION_BLOCK_CHARS)
    company_blocks = split_experience_by_companies(experience_raw)
    
    semaphore = asyncio.Semaphore(MAX_THREAD_WORKERS)

    async def translate_with_semaphore(text: str) -> str:
        if not text.strip():
            return ""
        async with semaphore:
            return await translate_ru_to_en_async(text, http_client, trace_id=trace_id)

    tasks = [translate_with_semaphore(b) for b in general_blocks]
    tasks.extend([translate_with_semaphore(block) for block in company_blocks])
    results = await asyncio.gather(*tasks)
    
    n_general = len(general_blocks)
    general_en = "\n\n".join(r for r in results[:n_general] if (r or "").strip())
    companies_en = results[n_general:]
    experience_en = "\n\n".join(c for c in companies_en if (c or "").strip())
    content = general_en + ("\n\n-----\n\n" + experience_en if experience_en else "")

    document_id = uuid4()
    # Split large document so each part keeps delimiter (experience not classified as general)
    if MAX_DOCUMENT_PART_CHARS and len(content) > MAX_DOCUMENT_PART_CHARS:
        parts = _split_content_preserving_delimiter(content, MAX_DOCUMENT_PART_CHARS)
        part_results = await asyncio.gather(
            *[asyncio.to_thread(_chunk_one_part, part) for part in parts]
        )
        chunks_text = []
        chunks_meta = []
        for texts, metas in part_results:
            chunks_text.extend(texts)
            chunks_meta.extend(metas)
    else:
        resume = build_resume_tree(content)
        has_general = bool(resume.general.content_blocks)
        has_companies = bool(resume.companies)
        if has_general or has_companies:
            chunks_with_meta = chunks_from_semantic_resume(
                resume,
                include_metadata=True,
                split_technologies=True,
                max_chunk_size=OLLAMA_CHUNK_MAX_SIZE,
                chunk_overlap=OLLAMA_CHUNK_OVERLAP,
            )
            chunks_text = [t for t, _ in chunks_with_meta]
            chunks_meta = [m for _, m in chunks_with_meta]
        else:
            chunks_text = chunk_text_sentence_aware(
                content,
                chunk_size=OLLAMA_CHUNK_MAX_SIZE,
                overlap=OLLAMA_CHUNK_OVERLAP,
            )
            chunks_meta = [{}] * len(chunks_text)
    if not chunks_text:
        return False, None, "No text chunks produced"

    log_trace(
        SERVICE_NAME,
        trace_id,
        OP_DB_WRITE,
        "postgres",
        target_resource="documents,chunks",
        extra={"job_id": job_id},
    )
    conn = await pool.acquire()
    try:
        async with conn.transaction():
            old_id = await find_document_by_owner_external(conn, owner_id, external_id)
            if old_id:
                old_chunk_ids = await get_chunk_ids_by_document_id(conn, old_id)
                await delete_chunks_and_mark_superseded(conn, old_id)
                if old_chunk_ids:
                    log_trace(
                        SERVICE_NAME,
                        trace_id,
                        OP_VECTOR_WRITE,
                        "qdrant",
                        target_resource=QDRANT_COLLECTION,
                        extra={"action": "delete_old_points"},
                    )
                    qdrant.delete(
                        collection_name=QDRANT_COLLECTION,
                        points_selector=PointIdsList(points=[str(x) for x in old_chunk_ids]),
                    )
            version = 1 if not old_id else 2
            await insert_document(
                conn,
                document_id,
                owner_id,
                external_id,
                filename,
                content_type,
                content,
                version,
                original_content=raw_content,
            )
    except Exception as e:
        await pool.release(conn)
        logger.exception("Pipeline error for job %s: %s", job_id, e)
        return False, None, str(e)

    chunk_ids = [uuid4() for _ in chunks_text]
    try:
        vectors = await embed_batch(chunks_text, http_client, trace_id=trace_id)
    except Exception as e:
        await pool.release(conn)
        logger.exception("Embed failed for job %s: %s", job_id, e)
        async with pool.acquire() as c2:
            async with c2.transaction():
                await set_document_failed(c2, document_id, str(e))
        return False, str(document_id), str(e)

    if len(vectors) != len(chunk_ids):
        await pool.release(conn)
        async with pool.acquire() as c2:
            async with c2.transaction():
                await set_document_failed(c2, document_id, "Embed count mismatch")
        return False, str(document_id), "Embed count mismatch"

    points = [
        PointStruct(
            id=str(cid),
            vector=vec,
            payload={
                "chunk_id": str(cid),
                "document_id": str(document_id),
                "owner_id": owner_id,
                "text": text,
                **meta,
            },
        )
        for cid, vec, meta, text in zip(chunk_ids, vectors, chunks_meta, chunks_text)
    ]
    try:
        log_trace(
            SERVICE_NAME,
            trace_id,
            OP_VECTOR_WRITE,
            "qdrant",
            target_resource=QDRANT_COLLECTION,
            extra={"points_count": len(points)},
        )
        qdrant.upsert(collection_name=QDRANT_COLLECTION, points=points)
    except Exception as e:
        await pool.release(conn)
        logger.exception("Qdrant upsert failed for job %s: %s", job_id, e)
        qdrant.delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=PointIdsList(points=[str(c) for c in chunk_ids]),
        )
        async with pool.acquire() as c2:
            async with c2.transaction():
                await set_document_failed(c2, document_id, str(e))
        return False, str(document_id), str(e)

    try:
        log_trace(
            SERVICE_NAME,
            trace_id,
            OP_DB_WRITE,
            "postgres",
            target_resource="chunks,documents",
            extra={"document_id": str(document_id)},
        )
        chunk_rows = [
            (cid, i, t, chunks_meta[i] if i < len(chunks_meta) else {})
            for i, (cid, t) in enumerate(zip(chunk_ids, chunks_text))
        ]
        async with conn.transaction():
            await insert_chunks(conn, document_id, chunk_rows)
            await set_document_indexed(conn, document_id)
    except Exception as e:
        logger.exception("Chunk insert failed for job %s: %s", job_id, e)
        qdrant.delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=PointIdsList(points=[str(c) for c in chunk_ids]),
        )
        await pool.release(conn)
        async with pool.acquire() as c2:
            async with c2.transaction():
                await set_document_failed(c2, document_id, str(e))
        return False, str(document_id), str(e)
    finally:
        await pool.release(conn)

    log_trace(
        SERVICE_NAME,
        trace_id,
        OP_QUEUE_PUBLISH,
        "rabbitmq",
        target_resource=RESULT_QUEUE,
        extra={"job_id": job_id, "document_id": str(document_id)},
    )
    await publish_result_fn(
        {"event": "indexing_completed", "job_id": job_id, "document_id": str(document_id), "trace_id": trace_id}
    )
    return True, str(document_id), None
