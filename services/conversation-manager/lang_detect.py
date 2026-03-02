"""Language detection on normalized text. Returns ISO 639-1 code.
System supports only Russian and English: Cyrillic present -> ru, else -> en."""

import logging
import re

from config import MODEL_WORKING_LANGUAGE

logger = logging.getLogger(__name__)

MIN_TEXT_LEN = 3

# Cyrillic Unicode block (Russian, etc.); used to treat text as Russian.
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")


def _has_cyrillic(text: str) -> bool:
    """Return True if text contains at least one Cyrillic character."""
    return bool(_CYRILLIC_RE.search(text))


def detect(normalized_text: str) -> str:
    """
    Detect language of normalized text. Returns 'ru' or 'en' only.
    If text contains Cyrillic -> Russian; otherwise English.
    Short or empty text returns MODEL_WORKING_LANGUAGE (no translation).
    """
    text = (normalized_text or "").strip()
    if len(text) < MIN_TEXT_LEN:
        return MODEL_WORKING_LANGUAGE
    return "ru" if _has_cyrillic(text) else "en"


def get_fallback_language() -> str:
    """Return language used for short/empty text (for tests)."""
    return MODEL_WORKING_LANGUAGE
