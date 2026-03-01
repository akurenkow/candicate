"""Assemble structured prompt from history, chunks, and subquery (FLOW-01)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from config import RAG_LABELS_PATH
from conversation_history import Turn
from token_budget import (
    estimate_tokens,
    trim_to_token_limit,
    summarize_older,
    CHARS_PER_TOKEN,
)

RESERVED_SYSTEM = 256
RESERVED_OUTPUT = 512

_DEFAULT_LABELS = {
    "user_prefix": "User: ",
    "assistant_prefix": "Assistant: ",
    "context_label": "Context (retrieved documents):",
    "question_prefix": "Question: ",
    "no_context": "(none)",
    "system_instruction": "You are the candidate described in the Context below. \"Context (retrieved documents):\" is the only source of truth. When asked about your identity, answer with the candidate's name and details from that Context. For any other question, answer only from the Context; do not invent facts. If something is not in the Context, say so.",
}

_CACHED_LABELS: dict[str, str] | None = None


def _load_rag_labels() -> dict[str, str]:
    global _CACHED_LABELS
    if _CACHED_LABELS is not None:
        return _CACHED_LABELS
    p = Path(RAG_LABELS_PATH)
    if not p.exists():
        _CACHED_LABELS = _DEFAULT_LABELS.copy()
        return _CACHED_LABELS
    labels = _DEFAULT_LABELS.copy()
    for line in p.read_text(encoding="utf-8").strip().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            labels[k.strip().lower()] = v.strip()
    _CACHED_LABELS = labels
    return _CACHED_LABELS


@dataclass(frozen=True)
class StructuredPrompt:
    """Final prompt for generation; total tokens <= model_limit."""
    text: str
    history_turns: list[Turn]
    context_tokens: int
    history_tokens: int
    query_tokens: int
    system_instruction: str | None = None


def _format_chunks(chunks: list[dict]) -> str:
    parts = []
    for c in chunks:
        text = (c.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n\n---\n\n".join(parts) if parts else ""


def _apply_budget(
    history: list[Turn],
    chunks_text: str,
    subquery: str,
    model_limit: int,
    history_keep_recent_n: int,
    summarizer: callable,
    max_summary_tokens: int,
) -> tuple[list[Turn], str]:
    chunk_tokens = estimate_tokens(chunks_text)
    query_tokens = estimate_tokens(subquery)
    reserved = RESERVED_SYSTEM + RESERVED_OUTPUT + chunk_tokens + query_tokens
    if reserved >= model_limit:
        return [], chunks_text
    history_budget = model_limit - reserved
    if not history or history_budget <= 0:
        return [], chunks_text
    if len(history) > history_keep_recent_n:
        history = summarize_older(
            history,
            history_keep_recent_n,
            summarizer,
            max_summary_tokens,
        )
    history = trim_to_token_limit(history, history_budget)
    return history, chunks_text


def build_prompt(
    history: list[Turn],
    chunks: list[dict],
    subquery: str,
    model_limit: int,
    *,
    history_keep_recent_n: int = 10,
    max_summary_tokens: int = 300,
    summarizer: Callable[[list[Turn]], str] | None = None,
) -> StructuredPrompt:
    """
    Combine history, retrieved chunks, and subquery into a structured prompt.
    Token budgeting: trim/summarize history so total never exceeds model_limit.
    """
    chunks_text = _format_chunks(chunks)
    summarizer_fn = summarizer or (lambda turns: " ".join(t.content for t in turns))

    history, _ = _apply_budget(
        history,
        chunks_text,
        subquery,
        model_limit,
        history_keep_recent_n,
        summarizer_fn,
        max_summary_tokens,
    )

    context_tokens = estimate_tokens(chunks_text)
    history_tokens = sum(estimate_tokens(t.content) for t in history)
    query_tokens = estimate_tokens(subquery)

    labels = _load_rag_labels()
    lines = []
    if history:
        for t in history:
            prefix = labels["user_prefix"] if t.role == "user" else labels["assistant_prefix"]
            lines.append(prefix + t.content)
        lines.append("")
    lines.append(labels["context_label"])
    context_body = chunks_text or labels["no_context"]
    question_line = labels["question_prefix"] + subquery
    prefix_block = "\n".join(lines) + "\n"
    suffix_block = "\n\n" + question_line
    prefix_tokens = estimate_tokens(prefix_block)
    suffix_tokens = estimate_tokens(suffix_block)
    context_budget = model_limit - prefix_tokens - suffix_tokens
    if context_budget < 0:
        context_budget = 0
    context_tokens_actual = estimate_tokens(context_body)
    if context_tokens_actual > context_budget:
        context_body = context_body[: context_budget * CHARS_PER_TOKEN].rstrip()
    text = prefix_block + context_body + suffix_block
    total = estimate_tokens(text)
    if total > model_limit:
        excess = total - model_limit
        text = text[: (len(text) - excess * CHARS_PER_TOKEN)].rstrip()

    return StructuredPrompt(
        text=text,
        history_turns=history,
        context_tokens=context_tokens,
        history_tokens=history_tokens,
        query_tokens=query_tokens,
        system_instruction=labels.get("system_instruction"),
    )
