"""Add pending_state to users for telegram bot (survive restart in WAITING_COVER_LETTER_VACANCY).

Revision ID: 014_add_pending_state
Revises: 013_add_used_by_to_hashes
Create Date: 2026-03-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "014_add_pending_state"
down_revision: Union[str, None] = "013_add_used_by_to_hashes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    exists = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'pending_state')"
    )).scalar()
    if not exists:
        op.add_column("users", sa.Column("pending_state", sa.Text(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    exists = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'pending_state')"
    )).scalar()
    if exists:
        op.drop_column("users", "pending_state")
