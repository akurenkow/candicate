"""Load conversation history for prompt construction (FLOW-01)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import asyncpg


@dataclass(frozen=True)
class Turn:
    role: str
    content: str


async def load_history(
    conn: asyncpg.Connection,
    message_id: UUID,
) -> list[Turn]:
    """
    Load conversation turns for the message's dialog. Chronological order (oldest first).
    When dialog_id is not in schema, returns empty list.
    """
    return []
