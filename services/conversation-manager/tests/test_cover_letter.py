"""Unit tests for cover letter (Executive Summary): prompt load, retrieval, generation, translation paths."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_path = Path(__file__).resolve().parent.parent
if str(_path) not in sys.path:
    sys.path.insert(0, str(_path))

from translation_client import TranslateResult
from generation_client import GenerateResult
from cover_letter import _load_system_prompt, run_cover_letter


def test_load_system_prompt_formatted_with_length_constraints():
    prompt = _load_system_prompt()
    assert "{min_chars}" not in prompt and "{max_chars}" not in prompt
    assert "character" in prompt.lower() or "length" in prompt.lower()
    assert any(c.isdigit() for c in prompt)


@pytest.mark.asyncio
async def test_run_cover_letter_empty_vacancy_returns_error():
    result = await run_cover_letter(
        owner_id="u1",
        vacancy_text="",
        trace_id="t1",
    )
    assert result.get("owner_id") == "u1"
    assert result.get("error") == "vacancy_text required"


@pytest.mark.asyncio
async def test_run_cover_letter_empty_retrieval_returns_no_documents():
    with patch("cover_letter.retrieval_search", new_callable=AsyncMock, return_value=[]):
        result = await run_cover_letter(
            owner_id="cand1",
            vacancy_text="Python developer",
            trace_id="t1",
        )
    assert result.get("owner_id") == "cand1"
    assert result.get("error") == "no_documents"
    assert "letter" not in result


@pytest.mark.asyncio
async def test_run_cover_letter_retrieval_exception_returns_retrieval_failed():
    with patch("cover_letter.retrieval_search", new_callable=AsyncMock, side_effect=RuntimeError("network")):
        result = await run_cover_letter(
            owner_id="u1",
            vacancy_text="Backend engineer",
            trace_id="t1",
        )
    assert result.get("owner_id") == "u1"
    assert result.get("error") == "retrieval_failed"


@pytest.mark.asyncio
async def test_run_cover_letter_english_vacancy_no_translation_calls():
    chunks = [{"text": "I built microservices at Company X.", "score": 0.8}]
    with patch("cover_letter.retrieval_search", new_callable=AsyncMock, return_value=chunks):
        with patch(
            "cover_letter.generation_generate",
            new_callable=AsyncMock,
            return_value=GenerateResult(success=True, output="Hello, I am interested in this role.", attempts=1),
        ):
            with patch("cover_letter.translation_translate", new_callable=AsyncMock) as m_trans:
                result = await run_cover_letter(
                    owner_id="u1",
                    vacancy_text="Python developer with microservices experience",
                    trace_id="t1",
                )
                m_trans.assert_not_called()
    assert result.get("owner_id") == "u1"
    assert result.get("letter") == "Hello, I am interested in this role."
    assert "error" not in result


@pytest.mark.asyncio
async def test_run_cover_letter_passes_owner_id_filter_and_vacancy_to_retrieval():
    chunks = [{"text": "snippet", "score": 0.7}]
    with patch("cover_letter.retrieval_search", new_callable=AsyncMock, return_value=chunks) as m_search:
        with patch(
            "cover_letter.generation_generate",
            new_callable=AsyncMock,
            return_value=GenerateResult(success=True, output="Letter.", attempts=1),
        ):
            await run_cover_letter(
                owner_id="cand_42",
                vacancy_text="Java developer",
                trace_id="t1",
                top_k=5,
            )
            m_search.assert_called_once()
            assert m_search.call_args.kwargs.get("filters") == {"owner_id": "cand_42"}
            assert m_search.call_args.kwargs.get("query") == "Java developer"
            assert m_search.call_args.kwargs.get("top_k") == 5


@pytest.mark.asyncio
async def test_run_cover_letter_generation_failure_returns_error():
    chunks = [{"text": "snippet", "score": 0.7}]
    with patch("cover_letter.retrieval_search", new_callable=AsyncMock, return_value=chunks):
        with patch(
            "cover_letter.generation_generate",
            new_callable=AsyncMock,
            return_value=GenerateResult(success=False, output="", error="timeout"),
        ):
            result = await run_cover_letter(
                owner_id="u1",
                vacancy_text="DevOps",
                trace_id="t1",
            )
    assert result.get("owner_id") == "u1"
    assert result.get("error") == "generation_failed"
    assert "letter" not in result


@pytest.mark.asyncio
async def test_run_cover_letter_russian_vacancy_translates_in_and_out():
    chunks = [{"text": "Resume excerpt", "score": 0.8}]
    vacancy_ru = "Разработчик Python с опытом микросервисов"
    vacancy_en = "Python developer with microservices experience"
    letter_en = "Hello, I am interested in this position."
    letter_ru = "Здравствуйте, меня заинтересовала эта позиция."
    with patch("cover_letter.retrieval_search", new_callable=AsyncMock, return_value=chunks):
        with patch(
            "cover_letter.generation_generate",
            new_callable=AsyncMock,
            return_value=GenerateResult(success=True, output=letter_en, attempts=1),
        ):
            with patch(
                "cover_letter.translation_translate",
                new_callable=AsyncMock,
                side_effect=[
                    TranslateResult(translated_text=vacancy_en, error=None),
                    TranslateResult(translated_text=letter_ru, error=None),
                ],
            ) as m_trans:
                result = await run_cover_letter(
                    owner_id="u1",
                    vacancy_text=vacancy_ru,
                    trace_id="t1",
                )
                assert m_trans.call_count == 2
                assert m_trans.call_args_list[0].kwargs.get("source_lang") == "ru"
                assert m_trans.call_args_list[0].kwargs.get("target_lang") == "en"
                assert m_trans.call_args_list[1].kwargs.get("source_lang") == "en"
                assert m_trans.call_args_list[1].kwargs.get("target_lang") == "ru"
    assert result.get("owner_id") == "u1"
    assert result.get("letter") == letter_ru
    assert "error" not in result


@pytest.mark.asyncio
async def test_run_cover_letter_vacancy_translation_failure_returns_error():
    with patch(
        "cover_letter.translation_translate",
        new_callable=AsyncMock,
        return_value=TranslateResult(translated_text=None, error="timeout"),
    ):
        result = await run_cover_letter(
            owner_id="u1",
            vacancy_text="Вакансия на русском",
            trace_id="t1",
        )
    assert result.get("owner_id") == "u1"
    assert result.get("error") == "vacancy_translation_failed"
    assert "letter" not in result


@pytest.mark.asyncio
async def test_run_cover_letter_letter_translation_failure_returns_error():
    chunks = [{"text": "snippet", "score": 0.8}]
    with patch("cover_letter.retrieval_search", new_callable=AsyncMock, return_value=chunks):
        with patch(
            "cover_letter.generation_generate",
            new_callable=AsyncMock,
            return_value=GenerateResult(success=True, output="English letter.", attempts=1),
        ):
            with patch(
                "cover_letter.translation_translate",
                new_callable=AsyncMock,
                side_effect=[
                    TranslateResult(translated_text="Vacancy in English", error=None),
                    TranslateResult(translated_text=None, error="timeout"),
                ],
            ):
                result = await run_cover_letter(
                    owner_id="u1",
                    vacancy_text="Текст вакансии",
                    trace_id="t1",
                )
    assert result.get("owner_id") == "u1"
    assert result.get("error") == "letter_translation_failed"
    assert "letter" not in result


@pytest.mark.asyncio
async def test_run_cover_letter_empty_chunk_texts_returns_no_documents():
    chunks = [{"text": "  ", "score": 0.8}, {"text": "", "score": 0.7}]
    with patch("cover_letter.retrieval_search", new_callable=AsyncMock, return_value=chunks):
        result = await run_cover_letter(
            owner_id="u1",
            vacancy_text="Engineer",
            trace_id="t1",
        )
    assert result.get("owner_id") == "u1"
    assert result.get("error") == "no_documents"
