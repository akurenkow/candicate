"""Deterministic tests for prompt assembly and token budgeting."""

import pytest

from conversation_history import Turn
from prompt_assembler import build_prompt, StructuredPrompt
from token_budget import estimate_tokens


def test_build_prompt_empty_history_no_chunks():
    p = build_prompt([], [], "What is X?", model_limit=4096)
    assert isinstance(p, StructuredPrompt)
    assert "Question: What is X?" in p.text
    assert "(none)" in p.text
    assert estimate_tokens(p.text) <= 4096


def test_build_prompt_respects_model_limit():
    chunks = [{"text": "x" * 5000}]
    history = [Turn("user", "h " * 1000)]
    p = build_prompt(history, chunks, "q", model_limit=100)
    assert estimate_tokens(p.text) <= 100


def test_build_prompt_includes_history_and_context():
    history = [Turn("user", "Hi"), Turn("assistant", "Hello")]
    chunks = [{"text": "Doc content"}]
    p = build_prompt(history, chunks, "Follow-up?", model_limit=2048)
    assert "User: Hi" in p.text
    assert "Assistant: Hello" in p.text
    assert "Doc content" in p.text
    assert "Follow-up?" in p.text
    assert p.history_turns
    assert p.context_tokens >= 1
    assert p.query_tokens >= 1


def test_build_prompt_summarizer_used_when_many_turns():
    def fixed_summarizer(turns):
        return "FIXED_SUMMARY"

    history = [Turn("user", "q" + str(i)) for i in range(15)]
    p = build_prompt(
        history,
        [],
        "last q",
        model_limit=2048,
        history_keep_recent_n=2,
        max_summary_tokens=20,
        summarizer=fixed_summarizer,
    )
    assert "[Previous context summary]" in p.text
    assert "FIXED_SUMMARY" in p.text
    assert estimate_tokens(p.text) <= 2048


def test_build_prompt_deterministic_same_input():
    history = [Turn("user", "a"), Turn("assistant", "b")]
    chunks = [{"text": "chunk1"}]
    p1 = build_prompt(history, chunks, "q", model_limit=1024)
    p2 = build_prompt(history, chunks, "q", model_limit=1024)
    assert p1.text == p2.text
    assert p1.history_tokens == p2.history_tokens
    assert p1.context_tokens == p2.context_tokens
