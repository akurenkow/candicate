"""
Normalize company name for case-insensitive and transliteration-consistent matching.
Used at ingestion (store normalized) and at retrieval (normalize filter value).
"""

from __future__ import annotations

# Russian Cyrillic -> Latin (1:1 for str.maketrans; single-char mapping)
_RU_LOWER = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
_RU_UPPER = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"
# ч->c, ш->s, щ->s, ж->z, ъ/ь->x (to keep length), ё->e
_LAT_LOWER = "abvgdeezzijklmnoprstufhccssxyxyeua"
_LAT_UPPER = "ABVGDEEZZIJKLMNOPRSTUFHCCSSXYXYEUA"
_CYRILLIC_TO_LATIN = str.maketrans(_RU_LOWER + _RU_UPPER, _LAT_LOWER + _LAT_UPPER)


def normalize_company_name(value: str | None) -> str:
    """
    Normalize for storage and filter matching: lowercase + Cyrillic to Latin.
    E.g. "ЛАНИТ-ТЕРКОМ" -> "lanit-terkom", "Lanit-Tercom" -> "lanit-tercom".
    """
    if not value or not isinstance(value, str):
        return ""
    s = value.strip()
    if not s:
        return ""
    s = s.translate(_CYRILLIC_TO_LATIN)
    return s.lower()
