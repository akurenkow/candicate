"""Token budgeting: estimation, trim excess, summarize older content (FLOW-01)."""

from __future__ import annotations

from typing import Callable

from conversation_history import Turn

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate (char-based)."""
    if not text:
        return 0
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def trim_to_token_limit(turns: list[Turn], max_tokens: int) -> list[Turn]:
    """
    Keep turns from the end until total content tokens <= max_tokens.
    If a single turn exceeds max_tokens, keep it and return it alone.
    """
    if max_tokens <= 0 or not turns:
        return []
    total = 0
    kept: list[Turn] = []
    for t in reversed(turns):
        n = estimate_tokens(t.content)
        if total + n > max_tokens and kept:
            break
        kept.insert(0, t)
        total += n
    return kept


def summarize_older(
    turns: list[Turn],
    keep_recent_n: int,
    summarizer: Callable[[list[Turn]], str],
    max_summary_tokens: int,
) -> list[Turn]:
    """
    Replace older turns (all but the last keep_recent_n) with a single summary turn.
    Summary is produced by summarizer(older_turns). If summary exceeds max_summary_tokens,
    it is truncated by character count (deterministic).
    """
    if keep_recent_n <= 0 or len(turns) <= keep_recent_n:
        return list(turns)
    older = turns[:-keep_recent_n]
    recent = turns[-keep_recent_n:]
    summary_text = summarizer(older)
    max_chars = max_summary_tokens * CHARS_PER_TOKEN
    if len(summary_text) > max_chars:
        summary_text = summary_text[:max_chars]
    summary_turn = Turn(role="assistant", content=("[Previous context summary] " + summary_text))
    return [summary_turn] + recent
