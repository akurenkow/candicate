"""
Unit tests for resume converter output format.

Validates that:
1. Structured resume text conforms to the target format (single "-----" separator, general/experience hierarchy).
2. Conversion from raw files in tests/data/raw produces output that passes format validation.

Converter fix plan (for prompt/parser):
- Input: already translated text; Russian resumes must be convertible back to Russian.
- Separator: exactly one line "-----" (5 dashes) between general and experience.
- General: FIO as one "-" item; "Специализации:" -> main block "Желаемые должности:"; all items with "-" (L1);
  in "Образование"/"Навыки" "--" (L2) allowed; main blocks separated by blank line.
  Optional main blocks: Дополнительная информация (Обо мне), Желаемые должности, Желаемая зарплата,
  Причина ухода..., Что хочет от нового места..., С чем не хочет/хочет работать, Ключевые навыки.
- Experience (1 project): L1 company, L2 Period/Position/Project name/Предметная область/Технологии/Обязанности, L3 achievements.
- Experience (2+ projects): L1 company, L2 Position/Period, L3 project blocks (Название проекта № N, ...), L4 achievements.
  Optional L3: "Дополнительная активность в компании", "Обязанности и достижения" with L4 items.
- Experience: L1 (company) blocks separated by blank line.
"""

import os
from pathlib import Path

import pytest

from resume_format_validator import validate_resume_structured_format

_TEST_DIR = Path(__file__).resolve().parent
_DATA_DIR = _TEST_DIR / "data"
_RAW_DIR = _DATA_DIR / "raw"


def _read_optional(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except (OSError, PermissionError):
        return None


# --- Fixtures: valid and invalid structured text ---

VALID_MINIMAL = """- ФИО: Иванов Иван Иванович
- Образование:
-- 2020: Высшее.
- Навыки:
-- Python, SQL

-----

- Компания: Example Ltd
-- Период: 2020 — настоящее время
-- Должность: Разработчик
-- Название проекта: Проект 1
-- Обязанности и достижения:
--- Достижение 1.
"""

VALID_WITH_BLANK_LINES = """- ФИО: Петров П.П.
- Желаемые должности:
- Разработчик

- Образование:
-- 2019: ВУЗ.

-----

- Компания: ООО Рога
-- Период: 2019 — 2021
-- Должность: Джуниор
-- Обязанности и достижения:
--- Задача 1.
"""

INVALID_TWO_SEPARATORS = """- ФИО: A

-----

- Компания: X
-- Период: 2020 — 2021

-----

- Компания: Y
"""

INVALID_GENERAL_NO_DASH = """Основная информация без дефиса
- ФИО: A

-----

- Компания: X
"""

INVALID_NO_SEPARATOR = """- ФИО: A
- Компания: X
"""


class TestResumeFormatValidator:
    """Validate structured format rules."""

    def test_valid_minimal_passes(self):
        errs = validate_resume_structured_format(VALID_MINIMAL)
        assert errs == [], "Expected no errors for valid minimal resume: %s" % errs

    def test_valid_with_blank_lines_passes(self):
        errs = validate_resume_structured_format(VALID_WITH_BLANK_LINES)
        assert errs == [], "Expected no errors: %s" % errs

    def test_two_separators_fail(self):
        errs = validate_resume_structured_format(INVALID_TWO_SEPARATORS)
        assert any("only once" in e.lower() or "1" in e for e in errs), "Expected error about single separator: %s" % errs

    def test_general_without_dash_fails(self):
        errs = validate_resume_structured_format(INVALID_GENERAL_NO_DASH)
        assert any("level-1" in e.lower() or "nesting" in e.lower() for e in errs), "Expected error about general nesting: %s" % errs

    def test_no_separator_fails(self):
        errs = validate_resume_structured_format(INVALID_NO_SEPARATOR)
        assert any("separator" in e.lower() or "missing" in e.lower() for e in errs), "Expected error about missing separator: %s" % errs


class TestResumeConversionFromRaw:
    """Conversion from tests/data/raw to updated format and format validation."""

    @pytest.fixture(scope="class")
    def raw_txt_files(self):
        if not _RAW_DIR.is_dir():
            return []
        return list(_RAW_DIR.glob("*.txt"))

    def test_converted_output_passes_format_validation(self, raw_txt_files):
        """Run conversion on each raw .txt and assert output passes format validation (integration: needs API)."""
        if not raw_txt_files:
            pytest.skip("No .txt files in tests/data/raw")
        try:
            import httpx
        except ImportError:
            pytest.skip("httpx not installed")
        api_url = os.getenv("API_GATEWAY_URL", "http://localhost:8001").rstrip("/")
        for path in raw_txt_files:
            raw_text = _read_optional(path)
            if raw_text is None:
                pytest.skip("Cannot read %s (permission or missing)" % path)
            try:
                with httpx.Client(timeout=120.0) as client:
                    r = client.post(
                        f"{api_url}/resume/convert",
                        json={"raw_text": raw_text[:50000]},
                    )
            except Exception as e:
                pytest.skip("Resume convert API not available: %s" % e)
            if r.status_code != 200:
                pytest.fail("Resume convert failed for %s: %s %s" % (path.name, r.status_code, r.text[:200]))
            data = r.json()
            structured = (data.get("structured_text") or "").strip()
            assert structured, "Empty structured_text for %s" % path.name
            errs = validate_resume_structured_format(structured)
            assert errs == [], "Format validation failed for %s: %s" % (path.name, errs)

    def test_structured_fixture_from_data_has_single_separator(self):
        """Fixture in tests/data: after normalizing separator to '-----', document has exactly one separator."""
        import re
        fixture_path = _DATA_DIR / "Куренков Антон Андреевич Резюме.txt"
        if not fixture_path.is_file():
            pytest.skip("Fixture %s not found" % fixture_path.name)
        text = _read_optional(fixture_path)
        if text is None:
            pytest.skip("Cannot read fixture")
        # Normalize: any line with 5+ dashes -> single "-----" so only one separator remains
        normalized = re.sub(r"^-{5,}[^\n]*$", "-----", text, flags=re.MULTILINE)
        errs = validate_resume_structured_format(normalized)
        # Must not report "only once" (multiple separators)
        sep_errors = [e for e in errs if "only once" in e.lower() or ("separator" in e.lower() and "missing" not in e.lower())]
        assert not sep_errors, "Fixture should have single separator after normalize: %s" % sep_errors
