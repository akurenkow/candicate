"""Unit tests for resume converter (nollm): convert 'Куренков Антон Андреевич.*' from raw and compare with gold.txt."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "tests" / "data" / "raw"
GOLD_PATH = RAW_DIR / "gold.txt"
RESUME_CONVERTER_DIR = ROOT / "services" / "resume-converter"
KURENKOV_GLOB = "Куренков Антон Андреевич.*"


def _normalize_for_compare(text: str) -> str:
    return text.strip().replace("\r\n", "\n") + "\n"


def _import_converter():
    if str(RESUME_CONVERTER_DIR) not in sys.path:
        sys.path.insert(0, str(RESUME_CONVERTER_DIR))
    from converter_impl import convert_file, convert_raw_text
    return convert_file, convert_raw_text


def _cleanup_conversion_results() -> None:
    """Remove any resume_*.txt in raw dir (leftover conversion outputs)."""
    if not RAW_DIR.is_dir():
        return
    for p in RAW_DIR.glob("resume_*.txt"):
        try:
            p.unlink()
        except OSError:
            pass


@pytest.fixture(scope="module")
def gold_content() -> str:
    if not GOLD_PATH.exists():
        pytest.skip(f"gold.txt not found: {GOLD_PATH}")
    return _normalize_for_compare(GOLD_PATH.read_text(encoding="utf-8", errors="ignore"))


@pytest.fixture(scope="module")
def raw_dir() -> Path:
    if not RAW_DIR.is_dir():
        pytest.skip(f"raw dir not found: {RAW_DIR}")
    return RAW_DIR


@pytest.fixture(scope="module")
def kurenkov_files(raw_dir: Path) -> list[Path]:
    files = sorted(raw_dir.glob(KURENKOV_GLOB))
    return [f for f in files if f.name != "gold.txt" and f.is_file()]


@pytest.fixture(scope="module", autouse=True)
def cleanup_after_module():
    yield
    _cleanup_conversion_results()


def test_convert_kurenkov_files_match_gold(kurenkov_files: list[Path], gold_content: str) -> None:
    """Convert each 'Куренков Антон Андреевич.*' file from raw and assert result equals gold.txt."""
    if not kurenkov_files:
        pytest.skip(f"No files matching {KURENKOV_GLOB} in {RAW_DIR}")
    convert_file, _ = _import_converter()
    for input_path in kurenkov_files:
        result = convert_file(input_path)
        assert result is not None, f"convert_file returned None for {input_path.name}"
        assert _normalize_for_compare(result) == gold_content, (
            f"Converted {input_path.name} did not match gold.txt"
        )


def test_convert_raw_text_produces_canonical_structure() -> None:
    """convert_raw_text on minimal HH-style text produces canonical format with separator."""
    _, convert_raw_text = _import_converter()
    raw = "Иванов Иван\n+7 999 000 0000\ntest@mail.ru\nПроживает: Москва"
    result = convert_raw_text(raw)
    assert "-----Секция опыта:-----" in result
    assert "- ФИО:" in result
    assert "Иванов Иван" in result
