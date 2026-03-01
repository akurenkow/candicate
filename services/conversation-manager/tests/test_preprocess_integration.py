"""Unit tests: preprocessing in worker lifecycle; BLOCK -> completed with fallback, OK -> normalized and completed."""

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

# Stub aio_pika so consumer can be imported without the queue library (tests only use process_one)
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

from db import create_pool, get_message, create_message
from config import FALLBACK_RESPONSE
from consumer import process_one


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
    user = os.getenv("POSTGRES_USER", "assistant_user")
    password = os.getenv("POSTGRES_PASSWORD", "strongpassword")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    from urllib.parse import quote_plus
    safe_pw = quote_plus(password)
    return f"postgresql://{user}:{safe_pw}@{host}:{port}/{test_db}"


@pytest.fixture
async def pool():
    db_url = _test_db_url()
    try:
        p = await asyncpg.create_pool(db_url, min_size=1, max_size=3, command_timeout=5)
    except Exception as e:
        pytest.skip(f"PostgreSQL unavailable: {e}")
    yield p
    await p.close()


@pytest.mark.asyncio
async def test_blocked_message_completed_with_fallback(pool):
    from clearing_client import PreprocessResult

    async with pool.acquire() as conn:
        mid = uuid4()
        await create_message(conn, message_id=mid, user_raw_text="bad word fuck here")
    with patch("consumer.clearing_preprocess", new_callable=AsyncMock) as m:
        m.return_value = PreprocessResult(normalized="bad word here", result="BLOCK", reasons=["profanity"])
        await process_one(pool, mid, "worker-1", trace_id="")
    async with pool.acquire() as conn:
        row = await get_message(conn, mid)
    assert row["status"] == "completed"
    assert row["assistant_text"] == FALLBACK_RESPONSE
    assert row["processing_lock"] is None


@pytest.mark.asyncio
async def test_ok_normalized_saved_and_completed(pool):
    from clearing_client import PreprocessResult
    from generation_client import GenerateResult

    async with pool.acquire() as conn:
        mid = uuid4()
        await create_message(conn, message_id=mid, user_raw_text="  What is Python?  ")
    with patch("subtask_retrieval.retrieval_search", new_callable=AsyncMock, return_value=[{"chunk_id": "1", "text": "doc", "score": 0.8}]):
        with patch("subtask_retrieval.generation_generate", new_callable=AsyncMock, return_value=GenerateResult(success=True, output="Python is a language.", attempts=1)):
            with patch("consumer.clearing_preprocess", new_callable=AsyncMock) as m:
                m.return_value = PreprocessResult(normalized="What is Python?", result="OK", reasons=[])
                await process_one(pool, mid, "worker-1", trace_id="")
    async with pool.acquire() as conn:
        row = await get_message(conn, mid)
    assert row["status"] == "completed"
    assert row["user_normalized_text"] == "What is Python?"
    assert row["processing_lock"] is None
