"""Unit tests: state transition validation."""

import os
import sys
from pathlib import Path

import pytest

_test_dir = Path(__file__).parent
_service_root = _test_dir.parent
if str(_service_root) not in sys.path:
    sys.path.insert(0, str(_service_root))

from state_machine import (
    can_transition,
    validate_transition,
    STATUS_QUEUED,
    STATUS_LOCKED,
    STATUS_PROCESSING,
    STATUS_COMPLETED,
    STATUS_ESCALATED,
    STATUS_FAILED,
    ALL_STATUSES,
    TERMINAL_STATUSES,
)


def test_allowed_queued_to_locked():
    assert can_transition(STATUS_QUEUED, STATUS_LOCKED) is True
    validate_transition(STATUS_QUEUED, STATUS_LOCKED)


def test_allowed_locked_to_processing():
    assert can_transition(STATUS_LOCKED, STATUS_PROCESSING) is True
    validate_transition(STATUS_LOCKED, STATUS_PROCESSING)


def test_allowed_locked_back_to_queued():
    assert can_transition(STATUS_LOCKED, STATUS_QUEUED) is True
    validate_transition(STATUS_LOCKED, STATUS_QUEUED)


def test_allowed_locked_to_completed():
    assert can_transition(STATUS_LOCKED, STATUS_COMPLETED) is True
    validate_transition(STATUS_LOCKED, STATUS_COMPLETED)


def test_allowed_processing_to_completed():
    assert can_transition(STATUS_PROCESSING, STATUS_COMPLETED) is True
    validate_transition(STATUS_PROCESSING, STATUS_COMPLETED)


def test_allowed_processing_to_escalated():
    assert can_transition(STATUS_PROCESSING, STATUS_ESCALATED) is True
    validate_transition(STATUS_PROCESSING, STATUS_ESCALATED)


def test_allowed_processing_to_failed():
    assert can_transition(STATUS_PROCESSING, STATUS_FAILED) is True
    validate_transition(STATUS_PROCESSING, STATUS_FAILED)


def test_disallowed_queued_to_processing():
    assert can_transition(STATUS_QUEUED, STATUS_PROCESSING) is False
    with pytest.raises(ValueError, match="Transition not allowed"):
        validate_transition(STATUS_QUEUED, STATUS_PROCESSING)


def test_disallowed_processing_to_locked():
    assert can_transition(STATUS_PROCESSING, STATUS_LOCKED) is False
    with pytest.raises(ValueError, match="Transition not allowed"):
        validate_transition(STATUS_PROCESSING, STATUS_LOCKED)


def test_disallowed_completed_to_any():
    assert can_transition(STATUS_COMPLETED, STATUS_QUEUED) is False
    assert can_transition(STATUS_COMPLETED, STATUS_PROCESSING) is False
    with pytest.raises(ValueError, match="Transition not allowed"):
        validate_transition(STATUS_COMPLETED, STATUS_QUEUED)


def test_disallowed_escalated_to_any():
    assert can_transition(STATUS_ESCALATED, STATUS_LOCKED) is False
    with pytest.raises(ValueError, match="Transition not allowed"):
        validate_transition(STATUS_ESCALATED, STATUS_LOCKED)


def test_invalid_status_raises():
    with pytest.raises(ValueError, match="Invalid status"):
        validate_transition("invalid", STATUS_QUEUED)
    with pytest.raises(ValueError, match="Invalid status"):
        validate_transition(STATUS_QUEUED, "invalid")


def test_terminal_statuses():
    assert STATUS_COMPLETED in TERMINAL_STATUSES
    assert STATUS_ESCALATED in TERMINAL_STATUSES
    assert STATUS_FAILED in TERMINAL_STATUSES
    assert STATUS_QUEUED not in TERMINAL_STATUSES
    assert len(TERMINAL_STATUSES) == 3


def test_all_statuses():
    assert len(ALL_STATUSES) == 6
    assert ALL_STATUSES >= {STATUS_QUEUED, STATUS_LOCKED, STATUS_PROCESSING, STATUS_COMPLETED, STATUS_ESCALATED, STATUS_FAILED}
