"""Unit tests for resume converter (nollm): convert 'Куренков Антон Андреевич.*' from raw and compare with gold.txt."""

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "tests" / "data" / "raw"
GOLD_PATH = RAW_DIR / "gold.txt"
RESUME_CONVERTER_DIR = ROOT / "services" / "resume-converter"
V4_SCRIPT = ROOT / "resume_converter_nollm_v4.py"
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


RESUME_BASE = "Куренков Антон Андреевич"
FORMAT_EXTS = (".txt", ".pdf", ".rtf", ".doc")


def test_convert_four_formats_same_output_and_size(raw_dir: Path) -> None:
    """Conversion of the same resume from .txt, .pdf, .rtf, .doc must yield identical output (content and size)."""
    paths = [raw_dir / (RESUME_BASE + ext) for ext in FORMAT_EXTS]
    missing = [p for p in paths if not p.exists()]
    if missing:
        pytest.skip(f"Missing resume files for 4-format test: {[p.name for p in missing]}")
    convert_file, _ = _import_converter()
    results = []
    for p in paths:
        out = convert_file(p)
        assert out is not None, f"convert_file returned None for {p.name}"
        normalized = _normalize_for_compare(out)
        results.append((len(normalized), normalized))
    sizes = [r[0] for r in results]
    contents = [r[1] for r in results]
    assert len(set(sizes)) == 1, f"Output sizes differ across formats: {list(zip([p.suffix for p in paths], sizes))}"
    assert len(set(contents)) == 1, "Output content differs across formats"


def test_four_sources_impl_and_v4_same_output_then_cleanup(raw_dir: Path) -> None:
    """Convert 4 sources (txt, pdf, rtf, doc) with impl and with v4; 8 output .txt must be identical; delete outputs after."""
    if not V4_SCRIPT.exists():
        pytest.skip(f"Reference script not found: {V4_SCRIPT}")
    source_paths = [raw_dir / (RESUME_BASE + ext) for ext in FORMAT_EXTS]
    missing = [p for p in source_paths if not p.exists()]
    if missing:
        pytest.skip(f"Missing sources: {[p.name for p in missing]}")
    convert_file, _ = _import_converter()
    impl_paths: list[Path] = []
    v4_paths: list[Path] = []
    try:
        with tempfile.TemporaryDirectory(prefix="resume_conv_") as tmp:
            tmp_path = Path(tmp)
            for ext in FORMAT_EXTS:
                src = raw_dir / (RESUME_BASE + ext)
                out_impl = tmp_path / f"impl_{ext[1:]}.txt"
                out_impl.write_text(convert_file(src), encoding="utf-8")
                impl_paths.append(out_impl)
            for src in source_paths:
                proc = subprocess.run(
                    [sys.executable, str(V4_SCRIPT), str(src)],
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                assert proc.returncode == 0, f"v4 failed for {src.name}: {proc.stderr!r}"
                lines = [s.strip() for s in proc.stdout.strip().splitlines() if s.strip()]
                out_v4 = Path(lines[0]) if lines else None
                assert out_v4 and out_v4.exists(), f"v4 did not create file for {src.name}: {proc.stdout!r}"
                v4_paths.append(out_v4)
            all_paths = impl_paths + v4_paths
            contents = [_normalize_for_compare(p.read_text(encoding="utf-8")) for p in all_paths]
            sizes = [len(c) for c in contents]
            assert len(set(contents)) == 1, "Impl and v4 outputs differ in content"
            assert len(set(sizes)) == 1, f"Output sizes differ: impl={sizes[:4]} v4={sizes[4:]}"
    finally:
        for p in v4_paths:
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
