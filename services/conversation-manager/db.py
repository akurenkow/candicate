"""PostgreSQL access for RAG messages (CTR-09). Async pool and message CRUD."""

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

import asyncpg

from config import DATABASE_URL
from state_machine import validate_transition

logger = logging.getLogger(__name__)


def _pg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg2"):
        url = url.replace("postgresql+psycopg2", "postgresql", 1)
    if "?" in url:
        url = url.split("?")[0]
    return url


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        _pg_url(DATABASE_URL),
        min_size=1,
        max_size=10,
        command_timeout=60,
    )


async def create_message(
    conn: asyncpg.Connection,
    message_id: UUID | None = None,
    user_raw_text: str = "",
    owner_id: str = "",
) -> UUID:
    mid = message_id or uuid4()
    now = datetime.now(timezone.utc)
    await conn.execute(
        """
        INSERT INTO rag_messages (id, user_raw_text, owner_id, status, created_at, updated_at)
        VALUES ($1, $2, $3, 'queued', $4, $4)
        """,
        mid,
        user_raw_text,
        owner_id or "",
        now,
    )
    return mid


async def get_message(conn: asyncpg.Connection, message_id: UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT id, user_raw_text, user_normalized_text, working_query, detected_lang,
               assistant_text, assistant_text_translated, status, processing_lock, locked_at,
               owner_id, error_message, failed_service, created_at, updated_at
        FROM rag_messages WHERE id = $1
        """,
        message_id,
    )


async def update_working_query(
    conn: asyncpg.Connection,
    message_id: UUID,
    working_query: str,
    detected_lang: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    await conn.execute(
        """
        UPDATE rag_messages SET working_query = $1, detected_lang = COALESCE($2, detected_lang), updated_at = $3 WHERE id = $4
        """,
        working_query,
        detected_lang,
        now,
        message_id,
    )


async def update_status(
    conn: asyncpg.Connection,
    message_id: UUID,
    from_status: str,
    to_status: str,
    *,
    user_normalized_text: str | None = None,
    assistant_text: str | None = None,
    assistant_text_translated: str | None = None,
    processing_lock: str | None = None,
    locked_at: datetime | None = None,
    error_message: str | None = None,
    failed_service: str | None = None,
) -> int:
    validate_transition(from_status, to_status)
    now = datetime.now(timezone.utc)
    result = await conn.execute(
        """
        UPDATE rag_messages SET
            status = $1, updated_at = $2,
            user_normalized_text = COALESCE($3, user_normalized_text),
            assistant_text = COALESCE($4, assistant_text),
            assistant_text_translated = $5,
            processing_lock = COALESCE($6, processing_lock),
            locked_at = COALESCE($7, locked_at),
            error_message = COALESCE($8, error_message),
            failed_service = COALESCE($9, failed_service)
        WHERE id = $10 AND status = $11
        """,
        to_status,
        now,
        user_normalized_text,
        assistant_text,
        assistant_text_translated,
        processing_lock,
        locked_at,
        error_message,
        failed_service,
        message_id,
        from_status,
    )
    return int(result.split()[-1])
