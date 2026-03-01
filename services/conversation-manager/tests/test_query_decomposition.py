"""Tests: query decomposition into atomic subqueries, validation (no empty, no duplicates)."""

import pytest
from uuid import uuid4

import sys
from pathlib import Path

_path = Path(__file__).parent.parent
if str(_path) not in sys.path:
    sys.path.insert(0, str(_path))

from query_decomposition import decompose, Subtask


def test_single_query_returns_one_subtask():
    mid = uuid4()
    result = decompose("What is Python?", mid)
    assert len(result) == 1
    assert result[0].query == "What is Python?"
    assert result[0].parent_message_id == mid
    assert result[0].index == 0


def test_compound_question_split_by_and():
    mid = uuid4()
    result = decompose("What is Python and what is JavaScript?", mid)
    assert len(result) >= 2
    qs = [s.query for s in result]
    assert "What is Python?" in qs or "what is Python?" in qs
    assert "what is JavaScript?" in qs


def test_compound_question_split_by_question_marks():
    mid = uuid4()
    result = decompose("What is X? What is Y? What is Z?", mid)
    assert len(result) == 3
    assert result[0].query.strip().endswith("?")
    assert result[1].query.strip().endswith("?")
    assert result[2].query.strip().endswith("?")


def test_no_empty_subtasks():
    mid = uuid4()
    result = decompose("  ?  ?  ", mid)
    assert len(result) == 0 or all(s.query.strip() for s in result)


def test_no_duplicate_subtasks():
    mid = uuid4()
    result = decompose("What is X? what is x?", mid)
    assert len(result) == 1


def test_subtask_ids_unique():
    mid = uuid4()
    result = decompose("A? B? C?", mid)
    ids = [s.subtask_id for s in result]
    assert len(ids) == len(set(ids))


def test_subtasks_tied_to_parent_message_id():
    mid = uuid4()
    result = decompose("Q1? Q2?", mid)
    for s in result:
        assert s.parent_message_id == mid


def test_empty_query_returns_empty_list():
    mid = uuid4()
    assert decompose("", mid) == []
    assert decompose("   ", mid) == []


def test_semicolon_split():
    mid = uuid4()
    result = decompose("Tell me about A; tell me about B", mid)
    assert len(result) >= 2
