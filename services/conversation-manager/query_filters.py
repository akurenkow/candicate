"""Extract structured filters from natural-language query for retrieval scoping."""

import re


# "Experience at X", "work at X", "job at X", "role at X"
_COMPANY_AT_PATTERN = re.compile(
    r"\b(?:experience|work|job|role)\s+at\s+([^.?!]+)",
    re.IGNORECASE,
)
# "at X" (trailing part of query)
_AT_TRAILING_PATTERN = re.compile(
    r"\bat\s+([^.?!]+?)\s*[.?!]?\s*$",
    re.IGNORECASE,
)


def extract_company_from_query(query: str) -> str | None:
    """
    If the query clearly asks about a specific company (e.g. "Experience at LANIT-TERKOM"),
    return that company name for use as company_name filter; otherwise None.
    """
    if not (query or "").strip():
        return None
    q = query.strip()
    m = _COMPANY_AT_PATTERN.search(q)
    if m:
        return m.group(1).strip() or None
    m = _AT_TRAILING_PATTERN.search(q)
    if m:
        return m.group(1).strip() or None
    return None
