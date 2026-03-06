"""Add last_restart_message_id to users for telegram bot (edit restart notice on next restart).

Revision ID: 015_add_last_restart_message_id
Revises: 014_add_pending_state
Create Date: 2026-03-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "015_add_last_restart_message_id"
down_revision: Union[str, None] = "014_add_pending_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    exists = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'last_restart_message_id')"
    )).scalar()
    if not exists:
        op.add_column("users", sa.Column("last_restart_message_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    exists = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'last_restart_message_id')"
    )).scalar()
    if exists:
        op.drop_column("users", "last_restart_message_id")
