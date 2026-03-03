"""PostgreSQL access for telegram bot users."""

import secrets
import os
from urllib.parse import quote_plus
from uuid import uuid4

import asyncpg


def _pg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg2"):
        url = url.replace("postgresql+psycopg2", "postgresql", 1)
    if "?" in url:
        url = url.split("?")[0]
    return url


def _get_database_url() -> str:
    host = os.getenv("POSTGRES_HOST", "").strip()
    if host or os.getenv("POSTGRES_USER", "").strip():
        return "postgresql://{user}:{pw}@{host}:{port}/{db}".format(
            user=os.getenv("POSTGRES_USER", "assistant_user").strip(),
            pw=quote_plus(os.getenv("POSTGRES_PASSWORD", "strongpassword").strip()),
            host=host or "postgres",
            port=os.getenv("POSTGRES_PORT", "5432").strip(),
            db=os.getenv("POSTGRES_DB", "assistant").strip(),
        )
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL or POSTGRES_* is required")
    return url


async def create_pool() -> asyncpg.Pool:
    url = _get_database_url()
    return await asyncpg.create_pool(
        _pg_url(url),
        min_size=1,
        max_size=5,
        command_timeout=30,
    )


async def get_user_by_telegram_id(conn: asyncpg.Connection, telegram_id: str) -> asyncpg.Record | None:
    row = await conn.fetchrow(
        "SELECT id, telegram_id, role, is_active, source, language, mode, dialog_owner_id, pending_state FROM users WHERE telegram_id = $1",
        str(telegram_id),
    )
    return row


async def set_pending_state(conn: asyncpg.Connection, telegram_id: str, state: str | None) -> int:
    """Set or clear persistent bot state (e.g. waiting_cover_letter_vacancy). Survives bot restart."""
    r = await conn.execute(
        "UPDATE users SET pending_state = $1 WHERE telegram_id = $2",
        state,
        str(telegram_id),
    )
    return int(r.split()[-1])


async def create_user(conn: asyncpg.Connection, telegram_id: str) -> None:
    await conn.execute(
        """
        INSERT INTO users (id, telegram_id, role, is_active, source)
        VALUES ($1, $2, NULL, true, 'telegram')
        """,
        uuid4(),
        str(telegram_id),
    )


async def get_chunk_count_by_document_id(conn: asyncpg.Connection, document_id: str) -> int:
    """Return chunk count for document (ingestion schema)."""
    val = await conn.fetchval(
        "SELECT COUNT(*) FROM chunks WHERE document_id = $1::uuid",
        document_id,
    )
    return val or 0


async def update_role(conn: asyncpg.Connection, telegram_id: str, role: str) -> int:
    r = await conn.execute(
        "UPDATE users SET role = $1 WHERE telegram_id = $2",
        role,
        str(telegram_id),
    )
    return int(r.split()[-1])


async def update_mode(conn: asyncpg.Connection, telegram_id: str, mode: str) -> int:
    r = await conn.execute(
        "UPDATE users SET mode = $1::user_mode WHERE telegram_id = $2",
        mode,
        str(telegram_id),
    )
    return int(r.split()[-1])


async def update_language(conn: asyncpg.Connection, telegram_id: str, language: str) -> int:
    r = await conn.execute(
        "UPDATE users SET language = $1 WHERE telegram_id = $2",
        language,
        str(telegram_id),
    )
    return int(r.split()[-1])


def generate_hash_token() -> str:
    """Generate secure random token (32 bytes, url-safe base64). Does not encode owner_id."""
    return secrets.token_urlsafe(32)


async def insert_hash(
    conn: asyncpg.Connection,
    owner_id: str,
    hash_value: str,
) -> None:
    """Insert hash record into hashes table. TTL 24h. owner_id stored in DB only, never exposed in token."""
    await conn.execute(
        """
        INSERT INTO hashes (id, owner_id, hash, is_valid, expires_at)
        VALUES (gen_random_uuid(), $1, $2, true, now() + interval '24 hours')
        """,
        owner_id,
        hash_value,
    )


async def link_hash(
    conn: asyncpg.Connection,
    telegram_id: str,
    hash_value: str,
) -> tuple[bool, str, str | None]:
    """
    Link HR to candidate via hash. Returns (success, message, owner_id or None).
    Validates: hash exists, is_valid, expires_at > now, used_by IS NULL or used_by = telegram_id.
    Re-sending same active token does not deactivate it.
    """
    tid = str(telegram_id)
    hv = hash_value.strip()
    row = await conn.fetchrow(
        """
        SELECT id, owner_id, used_by FROM hashes
        WHERE hash = $1 AND is_valid = true AND expires_at > now()
          AND (used_by IS NULL OR used_by = $2)
        """,
        hv,
        tid,
    )
    if row is None:
        return False, "Недействительный или истёкший хэш. / Invalid or expired hash.", None

    hash_id = row["id"]
    owner_id = row["owner_id"]
    if row["used_by"] == tid:
        return True, f"Связан с кандидатом {owner_id}. / Linked to candidate {owner_id}.", owner_id

    async with conn.transaction():
        await conn.execute(
            "UPDATE hashes SET is_valid = false WHERE used_by = $1 AND id != $2",
            tid,
            hash_id,
        )
        role_row = await conn.fetchrow("SELECT role FROM users WHERE telegram_id = $1", tid)
        if role_row and role_row["role"] == "admin":
            await conn.execute(
                "UPDATE users SET dialog_owner_id = $1, current_hash_uuid = $2 WHERE telegram_id = $3",
                owner_id,
                hash_id,
                tid,
            )
        else:
            await conn.execute(
                "UPDATE users SET role = $1, dialog_owner_id = $2, current_hash_uuid = $4 WHERE telegram_id = $3",
                "recruiter",
                owner_id,
                tid,
                hash_id,
            )
        await conn.execute(
            "UPDATE hashes SET used_by = $1 WHERE id = $2",
            tid,
            hash_id,
        )
    return True, f"Связан с кандидатом {owner_id}. / Linked to candidate {owner_id}.", owner_id
