"""Query decomposition: split working query into atomic subqueries, preserve semantic intent."""

import re
from dataclasses import dataclass
from uuid import UUID, uuid4


@dataclass(frozen=True)
class Subtask:
    subtask_id: UUID
    query: str
    parent_message_id: UUID
    index: int


def _split_compound(text: str) -> list[str]:
    t = text.strip()
    if not t:
        return []
    has_questions = "?" in t
    parts: list[str] = []
    for chunk in re.split(r"\?\s*", t):
        chunk = chunk.strip()
        if not chunk:
            continue
        for sub in re.split(r"\s+and\s+|\s+also\s+|\s+and\s+also\s+|\s*;\s*", chunk, flags=re.IGNORECASE):
            s = sub.strip()
            if not s:
                continue
            if has_questions and not s.rstrip().endswith(("?", ".", "!")):
                s = s.rstrip() + "?"
            parts.append(s)
    if not parts:
        parts = [t]
    return parts


def _normalize_for_dedup(s: str) -> str:
    return " ".join(s.lower().split())


def decompose(working_query: str, parent_message_id: UUID) -> list[Subtask]:
    """Split working query into atomic subqueries. Returns validated subtasks."""
    if not working_query or not working_query.strip():
        return []
    parts = _split_compound(working_query.strip())
    seen: set[str] = set()
    result: list[Subtask] = []
    for i, q in enumerate(parts):
        q = q.strip()
        if not q:
            continue
        norm = _normalize_for_dedup(q)
        if norm in seen:
            continue
        seen.add(norm)
        result.append(
            Subtask(
                subtask_id=uuid4(),
                query=q,
                parent_message_id=parent_message_id,
                index=i,
            )
        )
    return result
