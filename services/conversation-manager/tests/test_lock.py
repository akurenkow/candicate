"""Unit tests: lock exclusivity and stale lock recovery. Require PostgreSQL with rag_messages (migration 005)."""

import os
import sys
from pathlib import Path
from urllib.parse import quote_plus
from uuid import uuid4

import pytest
import asyncpg

_test_dir = Path(__file__).resolve().parent
_service_root = _test_dir.parent
# repo root = parent of "services" (where .env lives)
_project_root = _service_root.parent.parent
if str(_service_root) not in sys.path:
    sys.path.insert(0, str(_service_root))


def _load_env():
    env_path = _project_root / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_env()

from db import create_message, get_message
from lock import try_acquire_lock, release_lock, set_processing, release_stale_locks


def _test_db_url() -> str:
    u = os.getenv("DATABASE_URL", "").strip().replace("postgresql+psycopg2", "postgresql")
    test_db = os.getenv("POSTGRES_TEST_DB", "").strip() or os.getenv("POSTGRES_DB", "assistant")
    if u and test_db:
        from urllib.parse import urlparse, urlunparse
        parsed = list(urlparse(u))
        parsed[2] = "/" + test_db
        return urlunparse(parsed)
    if u:
        return u
    return "postgresql://{user}:{pw}@{host}:{port}/{db}".format(
        user=os.getenv("POSTGRES_USER", "assistant_user"),
        pw=quote_plus(os.getenv("POSTGRES_PASSWORD", "strongpassword")),
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        db=test_db,
    )


@pytest.fixture
def db_url():
    return _test_db_url()


@pytest.fixture
async def pool(db_url):
    try:
        p = await asyncpg.create_pool(db_url, min_size=1, max_size=3, command_timeout=5)
    except Exception as e:
        pytest.skip(f"PostgreSQL unavailable: {e}")
    yield p
    await p.close()


@pytest.fixture
async def queued_message(pool):
    async with pool.acquire() as conn:
        mid = uuid4()
        await create_message(conn, message_id=mid, user_raw_text="test")
        return mid


@pytest.mark.asyncio
async def test_only_one_worker_acquires_lock(pool, queued_message):
    """Two workers try to lock the same message; only one succeeds."""
    worker_a = "worker-a"
    worker_b = "worker-b"
    async with pool.acquire() as conn:
        acq_a = await try_acquire_lock(conn, queued_message, worker_a)
        acq_b = await try_acquire_lock(conn, queued_message, worker_b)
    assert acq_a is True
    assert acq_b is False
    async with pool.acquire() as conn:
        row = await get_message(conn, queued_message)
    assert row["processing_lock"] == worker_a
    assert row["status"] == "locked"


@pytest.mark.asyncio
async def test_release_then_another_worker_acquires(pool, queued_message):
    """After first worker releases (back to queued), second worker can acquire."""
    worker_a = "worker-a"
    worker_b = "worker-b"
    async with pool.acquire() as conn:
        await try_acquire_lock(conn, queued_message, worker_a)
        await release_lock(conn, queued_message, worker_a, back_to_queued=True)
        acq_b = await try_acquire_lock(conn, queued_message, worker_b)
    assert acq_b is True
    async with pool.acquire() as conn:
        row = await get_message(conn, queued_message)
    assert row["processing_lock"] == worker_b
    assert row["status"] == "locked"


@pytest.mark.asyncio
async def test_stale_lock_released_and_reacquirable(pool, monkeypatch):
    """Stale locked_at → release_stale_locks → message queued; another worker can acquire."""
    from datetime import datetime, timezone, timedelta
    import lock as lock_mod

    async with pool.acquire() as conn:
        mid = uuid4()
        await create_message(conn, message_id=mid, user_raw_text="stale test")
        await try_acquire_lock(conn, mid, "worker-old")
        await conn.execute(
            "UPDATE rag_messages SET locked_at = $1 WHERE id = $2",
            datetime.now(timezone.utc) - timedelta(seconds=400),
            mid,
        )
    monkeypatch.setattr(lock_mod, "LOCK_TTL_SECONDS", 300)
    async with pool.acquire() as conn:
        n = await release_stale_locks(conn)
    assert n >= 1
    async with pool.acquire() as conn:
        row = await get_message(conn, mid)
    assert row["status"] == "queued"
    assert row["processing_lock"] is None
    async with pool.acquire() as conn:
        acq = await try_acquire_lock(conn, mid, "worker-new")
    assert acq is True


@pytest.mark.asyncio
async def test_set_processing_only_holder(pool, queued_message):
    """set_processing succeeds only for the worker that holds the lock."""
    async with pool.acquire() as conn:
        await try_acquire_lock(conn, queued_message, "holder")
        ok_holder = await set_processing(conn, queued_message, "holder")
        ok_other = await set_processing(conn, queued_message, "other")
    assert ok_holder is True
    assert ok_other is False
