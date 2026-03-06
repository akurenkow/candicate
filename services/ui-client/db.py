"""PostgreSQL access for ui-client (users, hashes)."""

import secrets
import os
from urllib.parse import quote_plus
from uuid import uuid4

import asyncpg
from asyncpg.exceptions import UndefinedColumnError


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


async def get_user_by_external_id(conn: asyncpg.Connection, external_id: str) -> asyncpg.Record | dict | None:
    try:
        row = await conn.fetchrow(
            "SELECT id, telegram_id, role, is_active, source, language, mode, dialog_owner_id, pending_state, last_restart_message_id FROM users WHERE telegram_id = $1",
            str(external_id),
        )
        return row
    except UndefinedColumnError:
        try:
            row = await conn.fetchrow(
                "SELECT id, telegram_id, role, is_active, source, language, mode, dialog_owner_id, pending_state FROM users WHERE telegram_id = $1",
                str(external_id),
            )
            if row is None:
                return None
            return {**dict(row), "last_restart_message_id": None}
        except UndefinedColumnError:
            row = await conn.fetchrow(
                "SELECT id, telegram_id, role, is_active, source, language, mode, dialog_owner_id FROM users WHERE telegram_id = $1",
                str(external_id),
            )
            if row is None:
                return None
            return {**dict(row), "pending_state": None, "last_restart_message_id": None}


async def set_pending_state(conn: asyncpg.Connection, external_id: str, state: str | None) -> int:
    try:
        r = await conn.execute(
            "UPDATE users SET pending_state = $1 WHERE telegram_id = $2",
            state,
            str(external_id),
        )
        return int(r.split()[-1])
    except UndefinedColumnError:
        return 0


async def create_user(conn: asyncpg.Connection, external_id: str, source: str = "telegram") -> None:
    await conn.execute(
        """
        INSERT INTO users (id, telegram_id, role, is_active, source)
        VALUES ($1, $2, NULL, true, $3)
        """,
        uuid4(),
        str(external_id),
        source,
    )


async def get_chunk_count_by_document_id(conn: asyncpg.Connection, document_id: str) -> int:
    val = await conn.fetchval(
        "SELECT COUNT(*) FROM chunks WHERE document_id = $1::uuid",
        document_id,
    )
    return val or 0


async def update_role(conn: asyncpg.Connection, external_id: str, role: str) -> int:
    r = await conn.execute(
        "UPDATE users SET role = $1 WHERE telegram_id = $2",
        role,
        str(external_id),
    )
    return int(r.split()[-1])


async def update_mode(conn: asyncpg.Connection, external_id: str, mode: str) -> int:
    r = await conn.execute(
        "UPDATE users SET mode = $1::user_mode WHERE telegram_id = $2",
        mode,
        str(external_id),
    )
    return int(r.split()[-1])


async def update_language(conn: asyncpg.Connection, external_id: str, language: str) -> int:
    r = await conn.execute(
        "UPDATE users SET language = $1 WHERE telegram_id = $2",
        language,
        str(external_id),
    )
    return int(r.split()[-1])


def generate_hash_token() -> str:
    return secrets.token_urlsafe(32)


async def insert_hash(
    conn: asyncpg.Connection,
    owner_id: str,
    hash_value: str,
) -> None:
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
    external_id: str,
    hash_value: str,
) -> tuple[bool, str, str | None]:
    tid = str(external_id)
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


async def get_users_for_restart_notify(conn: asyncpg.Connection, source: str | None = None) -> list[tuple[str, int | None, str]]:
    """Return list of (telegram_id, last_restart_message_id or None, language)."""
    def _norm_lang(r: asyncpg.Record) -> str:
        lang = r.get("language", "en") or "en"
        return "ru" if (isinstance(lang, str) and lang.strip().lower() == "ru") else "en"

    if source:
        try:
            rows = await conn.fetch(
                "SELECT telegram_id, last_restart_message_id, language FROM users WHERE telegram_id IS NOT NULL AND source = $1",
                source,
            )
        except UndefinedColumnError:
            rows = await conn.fetch(
                "SELECT telegram_id, last_restart_message_id FROM users WHERE telegram_id IS NOT NULL AND source = $1",
                source,
            )
            return [(str(r["telegram_id"]), r.get("last_restart_message_id"), "en") for r in rows]
    else:
        try:
            rows = await conn.fetch(
                "SELECT telegram_id, last_restart_message_id, language FROM users WHERE telegram_id IS NOT NULL"
            )
        except UndefinedColumnError:
            rows = await conn.fetch(
                "SELECT telegram_id, last_restart_message_id FROM users WHERE telegram_id IS NOT NULL"
            )
            return [(str(r["telegram_id"]), r.get("last_restart_message_id"), "en") for r in rows]
    return [(str(r["telegram_id"]), r["last_restart_message_id"], _norm_lang(r)) for r in rows]


async def set_last_restart_message_id(conn: asyncpg.Connection, external_id: str, message_id: int) -> int:
    try:
        r = await conn.execute(
            "UPDATE users SET last_restart_message_id = $1 WHERE telegram_id = $2",
            message_id,
            str(external_id),
        )
        return int(r.split()[-1])
    except UndefinedColumnError:
        return 0
