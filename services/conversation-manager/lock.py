"""Atomic locking for RAG messages. Prevents concurrent processing by multiple workers."""

import logging
from datetime import datetime, timezone, timedelta
from uuid import UUID

import asyncpg

from config import LOCK_TTL_SECONDS
from state_machine import STATUS_QUEUED, STATUS_LOCKED, STATUS_PROCESSING

logger = logging.getLogger(__name__)


async def try_acquire_lock(
    conn: asyncpg.Connection,
    message_id: UUID,
    worker_id: str,
) -> bool:
    """Atomically claim message: queued and (no lock or lock expired) → locked by worker_id."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=LOCK_TTL_SECONDS)
    result = await conn.execute(
        """
        UPDATE rag_messages
        SET status = $1, processing_lock = $2, locked_at = $3, updated_at = $3
        WHERE id = $4 AND status = $5
          AND (processing_lock IS NULL OR locked_at < $6)
        """,
        STATUS_LOCKED,
        worker_id,
        now,
        message_id,
        STATUS_QUEUED,
        cutoff,
    )
    n = int(result.split()[-1])
    return n > 0


async def release_lock(
    conn: asyncpg.Connection,
    message_id: UUID,
    worker_id: str,
    back_to_queued: bool = False,
) -> bool:
    """Clear lock for message owned by worker_id. Optionally set status back to queued."""
    status_val = STATUS_QUEUED if back_to_queued else await conn.fetchval(
        "SELECT status FROM rag_messages WHERE id = $1", message_id
    )
    if status_val in ("completed", "escalated", "failed"):
        return True
    now = datetime.now(timezone.utc)
    result = await conn.execute(
        """
        UPDATE rag_messages
        SET processing_lock = NULL, locked_at = NULL, updated_at = $1
        """ + (", status = 'queued'" if back_to_queued else "") + """
        WHERE id = $2 AND processing_lock = $3
        """,
        now,
        message_id,
        worker_id,
    )
    n = int(result.split()[-1])
    return n > 0


async def set_processing(conn: asyncpg.Connection, message_id: UUID, worker_id: str) -> bool:
    """Transition locked → processing (caller must hold lock)."""
    result = await conn.execute(
        """
        UPDATE rag_messages SET status = $1, updated_at = $2
        WHERE id = $3 AND status = $4 AND processing_lock = $5
        """,
        STATUS_PROCESSING,
        datetime.now(timezone.utc),
        message_id,
        STATUS_LOCKED,
        worker_id,
    )
    return int(result.split()[-1]) > 0


async def release_stale_locks(conn: asyncpg.Connection) -> int:
    """Crash recovery: release locks older than LOCK_TTL_SECONDS, set status back to queued."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=LOCK_TTL_SECONDS)
    result = await conn.execute(
        """
        UPDATE rag_messages
        SET status = 'queued', processing_lock = NULL, locked_at = NULL, updated_at = $1
        WHERE status IN ('locked', 'processing') AND locked_at < $2
        """,
        datetime.now(timezone.utc),
        cutoff,
    )
    n = int(result.split()[-1])
    if n:
        logger.info("Released %d stale lock(s)", n)
    return n
