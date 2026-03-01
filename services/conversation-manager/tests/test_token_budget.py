"""Deterministic tests for token trimming and summarization."""

import pytest

from conversation_history import Turn
from token_budget import (
    CHARS_PER_TOKEN,
    estimate_tokens,
    trim_to_token_limit,
    summarize_older,
)


def test_estimate_tokens_empty():
    assert estimate_tokens("") == 0


def test_estimate_tokens_deterministic():
    assert estimate_tokens("a") == 1
    assert estimate_tokens("a" * 4) == 1
    assert estimate_tokens("a" * 5) == 2
    assert estimate_tokens("hello world") == 3


def test_trim_to_token_limit_empty_turns():
    assert trim_to_token_limit([], 100) == []


def test_trim_to_token_limit_zero_budget():
    turns = [Turn("user", "x")]
    assert trim_to_token_limit(turns, 0) == []


def test_trim_to_token_limit_keeps_from_end():
    turns = [
        Turn("user", "a" * 4),
        Turn("assistant", "b" * 4),
        Turn("user", "c" * 4),
    ]
    out = trim_to_token_limit(turns, 2)
    assert out == [Turn("assistant", "b" * 4), Turn("user", "c" * 4)]


def test_trim_to_token_limit_single_turn_over_budget():
    big = "x" * (4 * 10)
    turns = [Turn("user", big)]
    out = trim_to_token_limit(turns, 5)
    assert out == [Turn("user", big)]


def test_trim_to_token_limit_deterministic_order():
    turns = [
        Turn("user", "1"),
        Turn("assistant", "2"),
        Turn("user", "3"),
    ]
    out = trim_to_token_limit(turns, 10)
    assert out == turns
    out2 = trim_to_token_limit(turns, 1)
    assert len(out2) == 1
    assert out2[0] == Turn("user", "3")


def test_summarize_older_no_older():
    turns = [Turn("user", "a"), Turn("assistant", "b")]
    summarizer = lambda t: "summary"
    out = summarize_older(turns, 2, summarizer, 100)
    assert out == turns


def test_summarize_older_replace_older():
    turns = [
        Turn("user", "q1"),
        Turn("assistant", "a1"),
        Turn("user", "q2"),
        Turn("assistant", "a2"),
    ]
    summarizer = lambda t: "SUMMARY"
    out = summarize_older(turns, 2, summarizer, 100)
    assert len(out) == 3
    assert out[0].role == "assistant"
    assert "[Previous context summary] SUMMARY" == out[0].content
    assert out[1] == Turn("user", "q2")
    assert out[2] == Turn("assistant", "a2")


def test_summarize_older_truncates_by_max_summary_tokens():
    long_content = "word " * 200
    turns = [
        Turn("user", "q"),
        Turn("assistant", long_content),
        Turn("user", "last"),
    ]
    summarizer = lambda t: " ".join(x.content for x in t)
    max_summary_tokens = 10
    out = summarize_older(turns, 1, summarizer, max_summary_tokens)
    assert len(out) == 2
    summary_turn = out[0]
    assert estimate_tokens(summary_turn.content.replace("[Previous context summary] ", "")) <= max_summary_tokens


def test_summarize_older_deterministic_same_input_same_output():
    turns = [
        Turn("user", "u1"),
        Turn("assistant", "a1"),
        Turn("user", "u2"),
    ]
    summarizer = lambda t: "fixed"
    out1 = summarize_older(turns, 1, summarizer, 50)
    out2 = summarize_older(turns, 1, summarizer, 50)
    assert out1 == out2
