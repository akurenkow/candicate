"""Tests: post-process translate-back, semantic fidelity, retry limits, escalation, stored vs translated separation."""

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

_test_dir = Path(__file__).resolve().parent
_service_root = _test_dir.parent
_project_root = _service_root.parent.parent
for p in (str(_service_root), str(_project_root)):
    if p not in sys.path:
        sys.path.insert(0, p)

if "aio_pika" not in sys.modules:
    from unittest.mock import MagicMock
    sys.modules["aio_pika"] = MagicMock()


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

# Pin conversation-manager config and db so imports resolve here when ingestion tests ran first
_spec_c = importlib.util.spec_from_file_location("config", _service_root / "config.py")
_cm_config = importlib.util.module_from_spec(_spec_c)
_spec_c.loader.exec_module(_cm_config)
sys.modules["config"] = _cm_config
_spec_db = importlib.util.spec_from_file_location("db", _service_root / "db.py")
_cm_db = importlib.util.module_from_spec(_spec_db)
_spec_db.loader.exec_module(_cm_db)
sys.modules["db"] = _cm_db

from post_process import post_process, PostProcessResult


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
    return f"postgresql://{user}:{quote_plus(password)}@{host}:{port}/{test_db}"


@pytest.mark.asyncio
async def test_post_process_same_language_no_translation():
    result = await post_process("Answer in English.", "en", trace_id="")
    assert result.stored == "Answer in English."
    assert result.translated is None
    assert result.translation_failed is False


@pytest.mark.asyncio
async def test_post_process_empty_stored():
    result = await post_process("", "ru", trace_id="")
    assert result.stored == ""
    assert result.translated is None
    assert result.translation_failed is False


@pytest.mark.asyncio
async def test_post_process_multilingual_roundtrip_stored_vs_translated():
    from translation_client import TranslateResult
    with patch("post_process.translate_client", new_callable=AsyncMock) as m_trans:
        with patch("post_process.embed_batch_client", new_callable=AsyncMock) as m_embed:
            with patch("post_process.POSTPROCESS_FIDELITY_THRESHOLD", 0.0):
                m_trans.return_value = TranslateResult(translated_text="Ответ на русском", error=None)
                m_embed.return_value = [[0.1] * 64, [0.1] * 64]
                result = await post_process("Answer in English.", "ru", trace_id="")
    assert result.stored == "Answer in English."
    assert result.translated == "Ответ на русском"
    assert result.translation_failed is False


@pytest.mark.asyncio
async def test_post_process_fidelity_fail_retry_then_escalate():
    from translation_client import TranslateResult
    with patch("post_process.translate_client", new_callable=AsyncMock) as m_trans:
        with patch("post_process.embed_batch_client", new_callable=AsyncMock) as m_embed:
            with patch("post_process.POSTPROCESS_FIDELITY_THRESHOLD", 0.99):
                with patch("post_process.POSTPROCESS_TRANSLATION_RETRY_LIMIT", 2):
                    m_trans.return_value = TranslateResult(translated_text="Translated", error=None)
                    m_embed.return_value = [[1.0] * 64, [0.0] * 64]
                    result = await post_process("Original text.", "ru", trace_id="")
    assert result.stored == "Original text."
    assert result.translated is None
    assert result.translation_failed is True


@pytest.mark.asyncio
async def test_post_process_translate_failure_escalate():
    from translation_client import TranslateResult
    with patch("post_process.translate_client", new_callable=AsyncMock) as m_trans:
        with patch("post_process.POSTPROCESS_TRANSLATION_RETRY_LIMIT", 2):
            m_trans.return_value = TranslateResult(translated_text=None, error="timeout")
            result = await post_process("Original text.", "ru", trace_id="")
    assert result.stored == "Original text."
    assert result.translated is None
    assert result.translation_failed is True


@pytest.mark.asyncio
async def test_post_process_embed_failure_treated_as_fidelity_fail():
    from translation_client import TranslateResult
    with patch("post_process.translate_client", new_callable=AsyncMock) as m_trans:
        with patch("post_process.embed_batch_client", new_callable=AsyncMock) as m_embed:
            with patch("post_process.POSTPROCESS_TRANSLATION_RETRY_LIMIT", 2):
                m_trans.return_value = TranslateResult(translated_text="Translated", error=None)
                m_embed.return_value = None
                result = await post_process("Original.", "ru", trace_id="")
    assert result.stored == "Original."
    assert result.translated is None
    assert result.translation_failed is True


@pytest.fixture
async def pool():
    import asyncpg
    db_url = _test_db_url()
    try:
        p = await asyncpg.create_pool(db_url, min_size=1, max_size=3, command_timeout=5)
    except Exception as e:
        pytest.skip(f"PostgreSQL unavailable: {e}")
    try:
        async with p.acquire() as conn:
            await conn.fetchval("SELECT assistant_text_translated FROM rag_messages LIMIT 0")
    except Exception:
        await p.close()
        pytest.skip("Requires migration 007 (assistant_text_translated)")
    yield p
    await p.close()


@pytest.mark.asyncio
async def test_process_one_stores_stored_and_translated_separation(pool):
    """Ru query, empty aggregation: stored and translated columns written; translated is None."""
    from clearing_client import PreprocessResult
    from translation_client import TranslateResult
    get_message, create_message = _cm_db.get_message, _cm_db.create_message

    async with pool.acquire() as conn:
        mid = uuid4()
        await create_message(conn, message_id=mid, user_raw_text="Вопрос по Python")
    with patch("consumer.clearing_preprocess", new_callable=AsyncMock) as m_clear:
        m_clear.return_value = PreprocessResult(normalized="Вопрос по Python", result="OK", reasons=[])
        with patch("consumer.detect_lang", return_value="ru"):
            with patch("consumer.translate_query", new_callable=AsyncMock) as m_trans:
                m_trans.return_value = TranslateResult(translated_text="Question about Python", error=None)
                with patch("subtask_retrieval.retrieval_search", new_callable=AsyncMock, return_value=[{"chunk_id": "1", "text": "x"}]):
                    from consumer import process_one
                    await process_one(pool, mid, "worker-1", trace_id="")
    async with pool.acquire() as conn:
        row = await get_message(conn, mid)
    assert "assistant_text" in row
    assert row.get("assistant_text_translated") is None


@pytest.mark.asyncio
async def test_process_one_stored_vs_translated_when_generation_returns_text(pool):
    """When aggregation returns non-empty text and user lang != en, stored and translated are separate."""
    from clearing_client import PreprocessResult
    from translation_client import TranslateResult
    from subtask_result import SubtaskResult
    get_message, create_message = _cm_db.get_message, _cm_db.create_message

    async with pool.acquire() as conn:
        mid = uuid4()
        await create_message(conn, message_id=mid, user_raw_text="Скажи привет")
    with patch("consumer.clearing_preprocess", new_callable=AsyncMock) as m_clear:
        m_clear.return_value = PreprocessResult(normalized="Скажи привет", result="OK", reasons=[])
        with patch("consumer.detect_lang", return_value="ru"):
            with patch("consumer.translate_query", new_callable=AsyncMock) as m_trans:
                m_trans.return_value = TranslateResult(translated_text="Say hello", error=None)
                with patch("consumer._process_subtask", new_callable=AsyncMock) as m_sub:
                    m_sub.return_value = SubtaskResult.success("Model reply in English.")
                    with patch("post_process.translate_client", new_callable=AsyncMock) as pt:
                        with patch("post_process.embed_batch_client", new_callable=AsyncMock) as pe:
                            pt.return_value = TranslateResult(translated_text="Ответ модели на русском", error=None)
                            pe.return_value = [[0.2] * 64, [0.2] * 64]
                            from consumer import process_one
                            await process_one(pool, mid, "worker-1", trace_id="")
    async with pool.acquire() as conn:
        row = await get_message(conn, mid)
    assert row["assistant_text"] == "Model reply in English."
    assert row.get("assistant_text_translated") == "Ответ модели на русском"
    assert row["status"] == "completed"


@pytest.mark.asyncio
async def test_process_one_translation_failure_escalates(pool):
    from clearing_client import PreprocessResult
    from subtask_result import SubtaskResult
    from post_process import PostProcessResult
    get_message, create_message = _cm_db.get_message, _cm_db.create_message

    async with pool.acquire() as conn:
        mid = uuid4()
        await create_message(conn, message_id=mid, user_raw_text="Привет")
    with patch("consumer.clearing_preprocess", new_callable=AsyncMock) as m_clear:
        m_clear.return_value = PreprocessResult(normalized="Привет", result="OK", reasons=[])
        with patch("consumer.detect_lang", return_value="ru"):
            with patch("consumer.translate_query", new_callable=AsyncMock) as m_trans:
                from translation_client import TranslateResult
                m_trans.return_value = TranslateResult(translated_text="Hello", error=None)
                with patch("consumer._process_subtask", new_callable=AsyncMock) as m_sub:
                    m_sub.return_value = SubtaskResult.success("English reply.")
                    with patch("consumer.post_process", new_callable=AsyncMock) as m_pp:
                        m_pp.return_value = PostProcessResult(
                            stored="English reply.",
                            translated=None,
                            translation_failed=True,
                            translation_failure_reason="timeout",
                        )
                        from consumer import process_one
                        await process_one(pool, mid, "worker-1", trace_id="")
    async with pool.acquire() as conn:
        row = await get_message(conn, mid)
    # При эскалации из-за перевода ответ пользователю не сохраняем.
    assert row["assistant_text"] == ""
    assert row.get("assistant_text_translated") is None
    assert row["status"] == "escalated"
