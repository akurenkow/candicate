"""Extract structured filters from natural-language query for retrieval scoping."""

import re


# "Experience at X", "work at X", "job at X", "role at X"
_COMPANY_AT_PATTERN = re.compile(
    r"\b(?:experience|work|job|role)\s+at\s+([^.?!]+)",
    re.IGNORECASE,
)
# "Experience in X", "work in X", "job in X", "role in X"
_COMPANY_IN_PATTERN = re.compile(
    r"\b(?:experience|work|job|role)\s+in\s+([^.?!]+)",
    re.IGNORECASE,
)
# "at X" or "in X" (trailing part of query)
_AT_TRAILING_PATTERN = re.compile(
    r"\bat\s+([^.?!]+?)\s*[.?!]?\s*$",
    re.IGNORECASE,
)
_IN_TRAILING_PATTERN = re.compile(
    r"\bin\s+([^.?!]+?)\s*[.?!]?\s*$",
    re.IGNORECASE,
)
# Russian: "опыт в X", "опыте в X", "работа в X", "работе в X"
_COMPANY_RU_PATTERN = re.compile(
    r"\b(?:опыт|опыте|работа|работе)\s+в\s+([^.?!]+)",
    re.IGNORECASE,
)
# Russian: "в X" at end (e.g. "Расскажи об опыте в Ланит-Терком")
_RU_IN_TRAILING_PATTERN = re.compile(
    r"\bв\s+([^.?!]+?)\s*[.?!]?\s*$",
    re.IGNORECASE,
)


def extract_company_from_query(query: str) -> str | None:
    """
    If the query clearly asks about a specific company (e.g. "Experience at LANIT-TERKOM",
    "experience in Lanit-Terkom", "опыт в Ланит-Терком"), return that company name for use
    as company_name filter; otherwise None.
    """
    if not (query or "").strip():
        return None
    q = query.strip()
    m = _COMPANY_AT_PATTERN.search(q)
    if m:
        return m.group(1).strip() or None
    m = _COMPANY_IN_PATTERN.search(q)
    if m:
        return m.group(1).strip() or None
    m = _AT_TRAILING_PATTERN.search(q)
    if m:
        return m.group(1).strip() or None
    m = _IN_TRAILING_PATTERN.search(q)
    if m:
        return m.group(1).strip() or None
    m = _COMPANY_RU_PATTERN.search(q)
    if m:
        return m.group(1).strip() or None
    m = _RU_IN_TRAILING_PATTERN.search(q)
    if m:
        return m.group(1).strip() or None
    return None
