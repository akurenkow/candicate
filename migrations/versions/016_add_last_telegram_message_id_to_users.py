"""Add last_telegram_message_id to users (max message_id seen in chat; edit restart notice only if still last).

Revision ID: 016_add_last_telegram_message_id
Revises: 015_add_last_restart_message_id
Create Date: 2026-03-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "016_add_last_telegram_message_id"
down_revision: Union[str, None] = "015_add_last_restart_message_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    exists = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'last_telegram_message_id')"
    )).scalar()
    if not exists:
        op.add_column("users", sa.Column("last_telegram_message_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    exists = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'last_telegram_message_id')"
    )).scalar()
    if exists:
        op.drop_column("users", "last_telegram_message_id")
