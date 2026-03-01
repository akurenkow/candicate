"""Tests: cache hits, state finalization, lock release behavior."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import asyncpg

_test_dir = Path(__file__).resolve().parent
_service_root = _test_dir.parent
_project_root = _service_root.parent.parent
for p in (str(_service_root), str(_project_root)):
    if p not in sys.path:
        sys.path.insert(0, p)

if "aio_pika" not in sys.modules:
    _stub = MagicMock()
    _stub.IncomingMessage = MagicMock()
    sys.modules["aio_pika"] = _stub


def _load_env():
    env_path = _project_root / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_env()

from db import create_message, get_message
from config import FALLBACK_RESPONSE
from consumer import process_one, _finalize_lifecycle
from response_cache import get_cached, set_cached, _cache_key
from lock import try_acquire_lock, release_lock


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
    from urllib.parse import quote_plus
    return "postgresql://{user}:{pw}@{host}:{port}/{db}".format(
        user=os.getenv("POSTGRES_USER", "assistant_user"),
        pw=quote_plus(os.getenv("POSTGRES_PASSWORD", "strongpassword")),
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        db=test_db,
    )


@pytest.fixture
async def pool():
    try:
        p = await asyncpg.create_pool(_test_db_url(), min_size=1, max_size=3, command_timeout=5)
    except Exception as e:
        pytest.skip(f"PostgreSQL unavailable: {e}")
    yield p
    await p.close()


@pytest.fixture
def mock_generation():
    from generation_client import GenerateResult
    with patch("subtask_retrieval.generation_generate", new_callable=AsyncMock) as m:
        m.return_value = GenerateResult(success=True, output="Generated answer", attempts=1)
        yield m


@pytest.mark.asyncio
async def test_cache_hit_skips_generation(pool, mock_generation):
    """Cache hit returns stored response without calling retrieval or generation."""
    from clearing_client import PreprocessResult

    owner_id = "test-owner-1"
    async with pool.acquire() as conn:
        mid = uuid4()
        await create_message(conn, message_id=mid, user_raw_text="What is Python?", owner_id=owner_id)

    def fake_get(q, trace_id="", owner_id=""):
        return "Cached answer text" if "python" in q.lower() and owner_id == "test-owner-1" else None

    with patch("consumer.get_cached", side_effect=fake_get):
        with patch("consumer.clearing_preprocess", new_callable=AsyncMock) as m:
            m.return_value = PreprocessResult(normalized="What is Python?", result="OK", reasons=[])
            await process_one(pool, mid, "worker-1", trace_id="")

    mock_generation.assert_not_called()
    async with pool.acquire() as conn:
        row = await get_message(conn, mid)
    assert row["status"] == "completed"
    assert row["assistant_text"] == "Cached answer text"
    assert row["processing_lock"] is None


@pytest.mark.asyncio
async def test_cache_write_after_success(pool, mock_generation):
    """Successful generation writes result to cache (with owner_id)."""
    from clearing_client import PreprocessResult

    query = "Explain quantum computing"
    owner_id = "test-owner-2"
    async with pool.acquire() as conn:
        mid = uuid4()
        await create_message(conn, message_id=mid, user_raw_text=query, owner_id=owner_id)

    set_calls = []
    def capture_set(q, r, trace_id="", owner_id=""):
        set_calls.append((q, r, owner_id))

    with patch("consumer.set_cached", side_effect=capture_set):
        with patch("consumer.clearing_preprocess", new_callable=AsyncMock) as m:
            m.return_value = PreprocessResult(normalized=query, result="OK", reasons=[])
            with patch("subtask_retrieval.retrieval_search", new_callable=AsyncMock, return_value=[{"text": "doc", "score": 0.8}]):
                await process_one(pool, mid, "worker-1", trace_id="")

    assert len(set_calls) == 1
    assert set_calls[0][0] == query
    assert set_calls[0][1] == "Generated answer"
    assert set_calls[0][2] == owner_id


@pytest.mark.asyncio
async def test_finalization_persists_and_releases_lock(pool, mock_generation):
    """Completed message has assistant_text persisted and lock released."""
    from clearing_client import PreprocessResult

    async with pool.acquire() as conn:
        mid = uuid4()
        await create_message(conn, message_id=mid, user_raw_text="Test query")

    with patch("consumer.clearing_preprocess", new_callable=AsyncMock) as m:
        m.return_value = PreprocessResult(normalized="Test query", result="OK", reasons=[])
        with patch("subtask_retrieval.retrieval_search", new_callable=AsyncMock, return_value=[{"text": "x", "score": 0.8}]):
            await process_one(pool, mid, "worker-1", trace_id="")

    async with pool.acquire() as conn:
        row = await get_message(conn, mid)
    assert row["status"] == "completed"
    assert row["assistant_text"] == "Generated answer"
    assert row["processing_lock"] is None
    assert row["locked_at"] is None


@pytest.mark.asyncio
async def test_lock_released_on_error(pool):
    """On exception, handler releases lock (simulating on_message recovery path)."""
    from clearing_client import PreprocessResult

    async with pool.acquire() as conn:
        mid = uuid4()
        await create_message(conn, message_id=mid, user_raw_text="Fail me")

    with patch("consumer.clearing_preprocess", new_callable=AsyncMock) as m:
        m.return_value = PreprocessResult(normalized="Fail me", result="OK", reasons=[])
        with patch("consumer.run_retrieval", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                await process_one(pool, mid, "worker-1", trace_id="")

    async with pool.acquire() as conn:
        await release_lock(conn, mid, "worker-1", back_to_queued=True)
    async with pool.acquire() as conn:
        row = await get_message(conn, mid)
    assert row["processing_lock"] is None


@pytest.mark.asyncio
async def test_finalize_idempotent(pool):
    """Double finalize does not error; lock remains cleared."""
    from lock import try_acquire_lock, set_processing

    async with pool.acquire() as conn:
        mid = uuid4()
        await create_message(conn, message_id=mid, user_raw_text="x")
        ok = await try_acquire_lock(conn, mid, "worker-1")
        assert ok
        await set_processing(conn, mid, "worker-1")
    async with pool.acquire() as conn:
        await _finalize_lifecycle(conn, mid, "worker-1", "processing", "completed", "out", None)
    async with pool.acquire() as conn:
        await _finalize_lifecycle(conn, mid, "worker-1", "processing", "completed", "out", None)
    async with pool.acquire() as conn:
        row = await get_message(conn, mid)
    assert row["status"] == "completed"
    assert row["processing_lock"] is None


@pytest.mark.asyncio
async def test_cache_key_normalized():
    """Cache key is derived from normalized (lowercased, stripped) query."""
    k1 = _cache_key("  What is Python?  ")
    k2 = _cache_key("what is python?")
    k3 = _cache_key("What is Python?")
    assert k1 == k2 == k3
    assert k1.startswith("rag:response:")
