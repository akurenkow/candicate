"""Deterministic text normalization: remove technical noise, preserve semantics."""

import re
import unicodedata
from typing import Final

# Control and rarely-printable; preserve newline/tab for structure
_CONTROL_STRIP: Final[re.Pattern] = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]"
)
# Normalize all whitespace runs to single space; keep single \n
_WHITESPACE_NORM: Final[re.Pattern] = re.compile(r"[ \t\r\f\v]+")
_NEWLINE_NORM: Final[re.Pattern] = re.compile(r"\n+")
# Trim per line and overall
_LEADING_TRAILING_SPACE: Final[re.Pattern] = re.compile(r"^[ \t]+|[ \t]+$", re.MULTILINE)


def normalize(text: str) -> str:
    """
    Idempotent normalization: strip control chars, NFC, collapse whitespace, trim.
    Same input always yields same output.
    """
    if not isinstance(text, str):
        return ""
    s = _CONTROL_STRIP.sub("", text)
    s = unicodedata.normalize("NFC", s)
    s = _WHITESPACE_NORM.sub(" ", s)
    s = _NEWLINE_NORM.sub("\n", s)
    s = _LEADING_TRAILING_SPACE.sub("", s)
    return s.strip()
