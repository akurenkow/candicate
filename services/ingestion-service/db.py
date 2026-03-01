"""
PostgreSQL access for ingestion (CTR-19).
Async pool and document/chunk operations.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

import asyncpg

from config import DATABASE_URL

logger = logging.getLogger(__name__)

# Convert postgresql:// to postgresql for asyncpg (asyncpg uses postgresql://)
def _pg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg2"):
        return url.replace("postgresql+psycopg2", "postgresql", 1)
    return url


async def create_pool() -> asyncpg.Pool:
    """Create asyncpg connection pool."""
    return await asyncpg.create_pool(
        _pg_url(DATABASE_URL),
        min_size=1,
        max_size=10,
        command_timeout=60,
    )


async def find_document_by_owner_external(
    conn: asyncpg.Connection, owner_id: str, external_id: Optional[str]
) -> Optional[UUID]:
    """Return document id if exists (status not superseded)."""
    if external_id:
        row = await conn.fetchrow(
            """
            SELECT id FROM documents
            WHERE owner_id = $1 AND external_id = $2 AND status != 'superseded'
            ORDER BY version DESC LIMIT 1
            """,
            owner_id,
            external_id,
        )
    else:
        row = await conn.fetchrow(
            """
            SELECT id FROM documents
            WHERE owner_id = $1 AND external_id IS NULL AND status != 'superseded'
            ORDER BY version DESC LIMIT 1
            """,
            owner_id,
        )
    return row["id"] if row else None


async def delete_chunks_and_mark_superseded(
    conn: asyncpg.Connection, document_id: UUID
) -> None:
    """Delete chunks for document and set document status to superseded."""
    await conn.execute("DELETE FROM chunks WHERE document_id = $1", document_id)
    await conn.execute(
        "UPDATE documents SET status = 'superseded', updated_at = $1 WHERE id = $2",
        datetime.now(timezone.utc),
        document_id,
    )


async def insert_document(
    conn: asyncpg.Connection,
    document_id: UUID,
    owner_id: str,
    external_id: Optional[str],
    filename: str,
    content_type: str,
    content: str,
    version: int,
    original_content: Optional[str] = None,
) -> None:
    """Insert new document with status pending. original_content stores untranslated text."""
    now = datetime.now(timezone.utc)
    await conn.execute(
        """
        INSERT INTO documents (id, external_id, owner_id, filename, content_type, content, original_content, version, status, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending', $9, $9)
        """,
        document_id,
        external_id,
        owner_id,
        filename,
        content_type,
        content,
        original_content,
        version,
        now,
    )


async def insert_chunks(
    conn: asyncpg.Connection,
    document_id: UUID,
    chunks: list[tuple[UUID, int, str, Optional[dict]]],
) -> None:
    """Insert chunk rows. chunks = [(chunk_id, chunk_index, text, metadata?), ...]. metadata defaults to {}."""
    for row in chunks:
        cid, idx, text = row[0], row[1], row[2]
        meta = row[3] if len(row) > 3 and row[3] is not None else {}
        meta_json = json.dumps(meta)
        await conn.execute(
            "INSERT INTO chunks (id, document_id, chunk_index, text, metadata) VALUES ($1, $2, $3, $4, $5::jsonb)",
            cid,
            document_id,
            idx,
            text,
            meta_json,
        )


async def set_document_indexed(conn: asyncpg.Connection, document_id: UUID) -> None:
    """Set document status to indexed."""
    await conn.execute(
        "UPDATE documents SET status = 'indexed', updated_at = $1 WHERE id = $2",
        datetime.now(timezone.utc),
        document_id,
    )


async def set_document_failed(
    conn: asyncpg.Connection, document_id: UUID, error_message: str
) -> None:
    """Set document status to indexing_failed with message."""
    await conn.execute(
        "UPDATE documents SET status = 'indexing_failed', error_message = $1, updated_at = $2 WHERE id = $3",
        error_message[:4096],
        datetime.now(timezone.utc),
        document_id,
    )


async def get_chunk_ids_by_document_id(
    conn: asyncpg.Connection, document_id: UUID
) -> list[UUID]:
    """Return list of chunk ids for a document (for Qdrant rollback)."""
    rows = await conn.fetch("SELECT id FROM chunks WHERE document_id = $1", document_id)
    return [r["id"] for r in rows]


# --- ingestion_jobs (task status for api-gateway) ---

async def insert_job_pending(conn: asyncpg.Connection, job_id: str) -> None:
    """Insert job with status pending (idempotent: ignore if exists)."""
    await conn.execute(
        """
        INSERT INTO ingestion_jobs (job_id, status, updated_at)
        VALUES ($1, 'pending', $2)
        ON CONFLICT (job_id) DO NOTHING
        """,
        job_id,
        datetime.now(timezone.utc),
    )


async def ensure_job_pending(pool: asyncpg.Pool, job_id: str) -> None:
    """Ensure job exists with status pending (call when starting processing)."""
    async with pool.acquire() as conn:
        await insert_job_pending(conn, job_id)


async def set_job_completed(
    conn: asyncpg.Connection, job_id: str, document_id: str
) -> None:
    """Set job status to completed with document_id."""
    await conn.execute(
        """
        UPDATE ingestion_jobs SET status = 'completed', document_id = $1, updated_at = $2 WHERE job_id = $3
        """,
        document_id,
        datetime.now(timezone.utc),
        job_id,
    )


async def set_job_failed(
    conn: asyncpg.Connection, job_id: str, error_message: str
) -> None:
    """Set job status to failed with error_message."""
    await conn.execute(
        """
        UPDATE ingestion_jobs SET status = 'failed', error_message = $1, updated_at = $2 WHERE job_id = $3
        """,
        error_message[:4096],
        datetime.now(timezone.utc),
        job_id,
    )


async def set_job_completed_by_pool(
    pool: asyncpg.Pool, job_id: str, document_id: str
) -> None:
    async with pool.acquire() as conn:
        await set_job_completed(conn, job_id, document_id)


async def set_job_failed_by_pool(
    pool: asyncpg.Pool, job_id: str, error_message: str
) -> None:
    async with pool.acquire() as conn:
        await set_job_failed(conn, job_id, error_message)


async def get_job_status(
    pool: asyncpg.Pool, job_id: str
) -> Optional[dict]:
    """Return job row as dict (job_id, status, document_id, error_message, updated_at) or None."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT job_id, status, document_id, error_message, updated_at FROM ingestion_jobs WHERE job_id = $1",
            job_id,
        )
    return dict(row) if row else None
