# noqa: D100
"""
E2E: resume convert via API Gateway and structure compliance.

Calls POST /resume/convert with raw text from tests/data/raw (4 formats: txt, doc, rtf, pdf),
then asserts the converted document conforms to the canonical resume structure
(single '-----' separator, general/experience hierarchy, nesting levels).

Run: pytest tests/test_e2e_resume_convert_structure.py -v -m e2e
     (requires API_GATEWAY_URL and generation-service)
"""

import os
from pathlib import Path

import pytest

from resume_format_validator import assert_conforms_to_resume_structure, validate_resume_structured_format

_TEST_DIR = Path(__file__).resolve().parent
_DATA_DIR = _TEST_DIR / "data"
_RAW_DIR = _DATA_DIR / "raw"
BASE_URL = os.getenv("API_GATEWAY_URL", "http://localhost:8001").rstrip("/")
HTTP_TIMEOUT = 120
RESUME_BASE = "Куренков Антон Андреевич"
FORMATS = ("txt", "doc", "rtf", "pdf")


def _read_optional(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except (OSError, PermissionError):
        return None


def _raw_text_from_file(path: Path, ext: str) -> str | None:
    if ext == "txt":
        return _read_optional(path)
    try:
        raw_bytes = path.read_bytes()
    except (OSError, PermissionError):
        return None
    try:
        import sys
        _root = path.resolve().parents[2]
        _bot = _root / "services" / "telegram-bot"
        for p in (_root, _bot):
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
        from resume_convert import extract_resume_text
        return extract_resume_text(path.name, raw_bytes) or None
    except Exception:
        return None


def _convert_via_gateway(raw_text: str) -> str:
    import httpx
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        r = client.post(
            f"{BASE_URL}/resume/convert",
            json={"raw_text": raw_text[:50000]},
        )
    assert r.status_code == 200, "resume/convert: %s %s" % (r.status_code, r.text[:400])
    data = r.json()
    structured = (data.get("structured_text") or "").strip()
    assert structured, "resume/convert returned empty structured_text"
    return structured


@pytest.mark.e2e
@pytest.mark.parametrize("fmt", FORMATS)
def test_e2e_converted_document_in_format_conforms_to_structure(fmt: str):
    """E2E: Convert resume from each of 4 input formats via API Gateway; assert output conforms to structure."""
    path = _RAW_DIR / f"{RESUME_BASE}.{fmt}"
    if not path.is_file():
        pytest.skip("Raw file not found: %s" % path.name)
    raw_text = _raw_text_from_file(path, fmt)
    if not raw_text:
        pytest.skip("Could not get raw text for format %s" % fmt)
    structured = _convert_via_gateway(raw_text)
    assert_conforms_to_resume_structure(structured, message="Format %s" % fmt)


@pytest.mark.e2e
def test_e2e_resume_convert_returns_single_separator():
    """E2E: Convert .txt resume via API Gateway; result has exactly one '-----' separator."""
    path = _RAW_DIR / f"{RESUME_BASE}.txt"
    if not path.is_file():
        pytest.skip("Raw .txt not found")
    raw_text = _read_optional(path)
    if not raw_text:
        pytest.skip("Cannot read raw .txt")
    structured = _convert_via_gateway(raw_text)
    errors = validate_resume_structured_format(structured)
    sep_errors = [e for e in errors if "separator" in e.lower() and "only once" in e.lower()]
    assert not sep_errors, "Converted document must have single separator: %s" % sep_errors
