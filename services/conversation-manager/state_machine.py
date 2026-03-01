"""Strict state machine for RAG message lifecycle. Queued → locked → processing → completed|escalated|failed."""

from typing import Set, Tuple

STATUS_QUEUED = "queued"
STATUS_LOCKED = "locked"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_ESCALATED = "escalated"
STATUS_FAILED = "failed"

ALL_STATUSES = frozenset({
    STATUS_QUEUED,
    STATUS_LOCKED,
    STATUS_PROCESSING,
    STATUS_COMPLETED,
    STATUS_ESCALATED,
    STATUS_FAILED,
})

TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_ESCALATED, STATUS_FAILED})

ALLOWED_TRANSITIONS: Set[Tuple[str, str]] = {
    (STATUS_QUEUED, STATUS_LOCKED),
    (STATUS_LOCKED, STATUS_PROCESSING),
    (STATUS_LOCKED, STATUS_QUEUED),  # crash recovery: release lock
    (STATUS_LOCKED, STATUS_COMPLETED),  # security block: fallback response
    (STATUS_PROCESSING, STATUS_COMPLETED),
    (STATUS_PROCESSING, STATUS_ESCALATED),
    (STATUS_PROCESSING, STATUS_FAILED),
}


def can_transition(from_status: str, to_status: str) -> bool:
    return (from_status, to_status) in ALLOWED_TRANSITIONS


def validate_transition(from_status: str, to_status: str) -> None:
    if from_status not in ALL_STATUSES or to_status not in ALL_STATUSES:
        raise ValueError(f"Invalid status: {from_status!r} or {to_status!r}")
    if not can_transition(from_status, to_status):
        raise ValueError(f"Transition not allowed: {from_status!r} -> {to_status!r}")
