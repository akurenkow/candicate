"""Tests for vacancy_extract: PDF and other formats."""

import sys
from pathlib import Path

import pytest

_service_root = Path(__file__).resolve().parent.parent
if str(_service_root) not in sys.path:
    sys.path.insert(0, str(_service_root))

from vacancy_extract import (
    VACANCY_ALLOWED_EXT,
    extract_vacancy_text,
    is_vacancy_file,
)


def test_is_vacancy_file_accepts_pdf():
    assert is_vacancy_file("vacancy.pdf") is True
    assert is_vacancy_file("vacancy-test3.pdf") is True
    assert is_vacancy_file("x.PDF") is True


def test_is_vacancy_file_accepts_doc_txt():
    assert is_vacancy_file("v.docx") is True
    assert is_vacancy_file("v.doc") is True
    assert is_vacancy_file("v.txt") is True


def test_is_vacancy_file_rejects_other():
    assert is_vacancy_file("v.png") is False
    assert is_vacancy_file("v") is False


def test_extract_txt():
    assert extract_vacancy_text("a.txt", b"Hello world") == "Hello world"


def test_extract_pdf_invalid_bytes_returns_none():
    """Invalid PDF bytes must not crash and must return None."""
    assert extract_vacancy_text("a.pdf", b"not a pdf") is None
    assert extract_vacancy_text("vacancy-test3.pdf", b"\x00\x01") is None


def test_extract_pdf_empty_returns_none():
    """Empty or non-PDF bytes for .pdf extension return None."""
    assert extract_vacancy_text("a.pdf", b"") is None


def test_extract_unknown_extension_returns_none():
    assert extract_vacancy_text("a.xyz", b"data") is None


@pytest.mark.parametrize("ext", VACANCY_ALLOWED_EXT)
def test_allowed_extensions_are_recognized(ext):
    assert is_vacancy_file("file" + ext) is True
