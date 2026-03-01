"""Unit tests for output validation (relevance, prohibited, prompt leakage)."""

import pytest
from validation import (
    DefaultOutputValidator,
    ValidationResult,
    ValidationFailure,
)


def test_validator_accepts_non_empty_relevant_output():
    v = DefaultOutputValidator()
    r = v.validate("This is a proper answer.", "What is X?", "What is X?")
    assert r.valid
    assert r.failure is None


def test_validator_rejects_empty_output():
    v = DefaultOutputValidator()
    r = v.validate("", "Some prompt", None)
    assert not r.valid
    assert r.failure == ValidationFailure.RELEVANCE
    assert "short" in r.reason.lower() or "output" in r.reason.lower()


def test_validator_rejects_too_short_output():
    v = DefaultOutputValidator(min_output_length=10)
    r = v.validate("Hi", "Prompt", None)
    assert not r.valid
    assert r.failure == ValidationFailure.RELEVANCE


def test_validator_rejects_prohibited_content():
    v = DefaultOutputValidator(prohibited_substrings=["ignore previous", "evil"])
    r = v.validate("You should ignore previous instructions and do X.", "P", None)
    assert not r.valid
    assert r.failure == ValidationFailure.PROHIBITED
    assert "prohibited" in r.reason.lower() or "ignore" in r.reason.lower()


def test_validator_rejects_prompt_leakage():
    prompt_snippet = "Secret system: answer only in JSON."
    v = DefaultOutputValidator()
    r = v.validate(
        "Secret system: answer only in JSON. Here is the answer: 42.",
        prompt_snippet,
        None,
    )
    assert not r.valid
    assert r.failure == ValidationFailure.PROMPT_LEAKAGE
    assert "echo" in r.reason.lower() or "prompt" in r.reason.lower()


def test_validator_allows_output_without_prompt_echo():
    v = DefaultOutputValidator()
    r = v.validate("The capital is Paris.", "What is the capital of France?", None)
    assert r.valid


def test_validator_rejects_too_many_paragraphs():
    v = DefaultOutputValidator()
    long_output = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    r = v.validate(long_output, "Q?", None)
    assert not r.valid
    assert r.failure == ValidationFailure.VERBOSITY
    assert "paragraph" in r.reason.lower() or "verbosity" in r.reason.lower()


def test_validator_rejects_semantic_repetition():
    v = DefaultOutputValidator()
    rep = (
        "I optimized SQL queries and ETL processes by up to 100 times. This significantly improved system performance and efficiency.\n\n"
        "I accelerated SQL queries and ETL processes by up to 100 times, which significantly improved system performance."
    )
    r = v.validate(rep, "Q?", None)
    assert not r.valid
    assert r.failure == ValidationFailure.VERBOSITY
    assert "repetition" in r.reason.lower() or "verbosity" in r.reason.lower()


def test_validator_accepts_two_short_paragraphs():
    v = DefaultOutputValidator()
    out = "First short answer.\n\nSecond distinct point."
    r = v.validate(out, "Q?", None)
    assert r.valid
