"""Tests: language detection, translation, working_query storage, fallback on translation failure."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch
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
    from unittest.mock import MagicMock
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
    from urllib.parse import quote_plus
    user = os.getenv("POSTGRES_USER", "assistant_user")
    password = os.getenv("POSTGRES_PASSWORD", "strongpassword")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
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
async def test_same_language_working_query_equals_normalized(pool):
    from clearing_client import PreprocessResult

    async with pool.acquire() as conn:
        mid = uuid4()
        await create_message(conn, message_id=mid, user_raw_text="What is Python?")
    with patch("consumer.clearing_preprocess", new_callable=AsyncMock) as m_clear:
        m_clear.return_value = PreprocessResult(normalized="What is Python?", result="OK", reasons=[])
        with patch("consumer.detect_lang", return_value="en"):
            await process_one(pool, mid, "worker-1", trace_id="")
    async with pool.acquire() as conn:
        row = await get_message(conn, mid)
    assert row["user_normalized_text"] == "What is Python?"
    assert row["working_query"] == "What is Python?"
    assert row["detected_lang"] == "en"


@pytest.mark.asyncio
async def test_multilingual_translation_stored_as_working_query(pool):
    from clearing_client import PreprocessResult

    async with pool.acquire() as conn:
        mid = uuid4()
        await create_message(conn, message_id=mid, user_raw_text="Привет мир")
    with patch("consumer.clearing_preprocess", new_callable=AsyncMock) as m_clear:
        m_clear.return_value = PreprocessResult(normalized="Привет мир", result="OK", reasons=[])
        with patch("consumer.detect_lang", return_value="ru"):
            with patch("consumer.translate_query", new_callable=AsyncMock) as m_trans:
                from translation_client import TranslateResult
                m_trans.return_value = TranslateResult(translated_text="Hello world", error=None)
                await process_one(pool, mid, "worker-1", trace_id="")
    async with pool.acquire() as conn:
        row = await get_message(conn, mid)
    assert row["user_normalized_text"] == "Привет мир"
    assert row["working_query"] == "Hello world"
    assert row["detected_lang"] == "ru"


@pytest.mark.asyncio
async def test_translation_failure_fallback_working_query_unchanged(pool):
    from clearing_client import PreprocessResult
    from translation_client import TranslateResult

    async with pool.acquire() as conn:
        mid = uuid4()
        await create_message(conn, message_id=mid, user_raw_text="Что такое Python?")
    with patch("subtask_retrieval.retrieval_search", new_callable=AsyncMock, return_value=[{"chunk_id": "1", "text": "doc", "score": 0.8}]):
        with patch("subtask_retrieval.generation_generate", new_callable=AsyncMock) as m_gen:
            from generation_client import GenerateResult
            from post_process import PostProcessResult
            m_gen.return_value = GenerateResult(success=True, output="Answer", attempts=1)
            with patch("consumer.post_process", new_callable=AsyncMock) as m_pp:
                m_pp.return_value = PostProcessResult(stored="Answer", translated="Ответ", translation_failed=False)
                with patch("consumer.clearing_preprocess", new_callable=AsyncMock) as m_clear:
                    m_clear.return_value = PreprocessResult(normalized="Что такое Python?", result="OK", reasons=[])
                    with patch("consumer.detect_lang", return_value="ru"):
                        with patch("consumer.translate_query", new_callable=AsyncMock) as m_trans:
                            m_trans.return_value = TranslateResult(translated_text=None, error="translation_http_error:502")
                            await process_one(pool, mid, "worker-1", trace_id="")
    async with pool.acquire() as conn:
        row = await get_message(conn, mid)
    assert row["user_normalized_text"] == "Что такое Python?"
    assert row["working_query"] == "Что такое Python?"
    assert row["detected_lang"] == "ru"
    assert row["status"] == "completed"


# --- Unit tests for lang_detect (Cyrillic -> ru, else en) ---

def test_lang_detect_cyrillic_is_russian():
    from lang_detect import detect
    assert detect("привет") == "ru"
    assert detect("здравствуйте") == "ru"
    assert detect("Расскажите о вашем опыте в ML") == "ru"
    assert detect("Завтрак") == "ru"
    assert detect("Hi привет") == "ru"


def test_lang_detect_no_cyrillic_is_english():
    from lang_detect import detect
    assert detect("Hello") == "en"
    assert detect("Tell me about yourself") == "en"
    assert detect("What is Python?") == "en"


def test_lang_detect_short_or_empty_returns_model_language():
    from lang_detect import detect
    from config import MODEL_WORKING_LANGUAGE
    assert detect("") == MODEL_WORKING_LANGUAGE
    assert detect("  ") == MODEL_WORKING_LANGUAGE
    assert detect("ab") == MODEL_WORKING_LANGUAGE
    assert detect("х") == MODEL_WORKING_LANGUAGE  # single Cyrillic still short
