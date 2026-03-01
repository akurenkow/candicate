"""Unit tests: deterministic text normalization, idempotence, edge cases."""

import os
import sys
from pathlib import Path

import pytest

_test_dir = Path(__file__).resolve().parent
_service_root = _test_dir.parent
if str(_service_root) not in sys.path:
    sys.path.insert(0, str(_service_root))

from normalize import normalize


def test_empty():
    assert normalize("") == ""
    assert normalize("   ") == ""
    assert normalize("\t\n  \n\t") == ""


def test_idempotent():
    t = "  hello   world  \n\n  foo  "
    assert normalize(t) == normalize(normalize(t))


def test_collapse_whitespace():
    assert normalize("a   b\t\tc") == "a b c"
    assert normalize("a \n \n b") == "a\n\nb"  # newlines not collapsed when separated by spaces


def test_trim():
    assert normalize("  x  ") == "x"
    assert normalize("\n\n x \n\n") == "x"


def test_control_stripped():
    assert "\x00" not in normalize("a\x00b")
    assert "\x1f" not in normalize("a\x1fb")
    assert normalize("a\x00\x07b") == "ab"


def test_unicode_nfc():
    # NFD vs NFC
    nfd = "cafe\u0301"
    assert normalize(nfd) == "café"


def test_preserves_semantics():
    assert normalize("What is your experience?") == "What is your experience?"
    assert normalize("  Multiple   spaces  ") == "Multiple spaces"


def test_non_string_returns_empty():
    assert normalize(None) == ""
    assert normalize(123) == ""
