"""Add used_by to hashes table for /link command.

Revision ID: 013_add_used_by_to_hashes
Revises: 012_create_hashes_table
Create Date: 2026-02-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "013_add_used_by_to_hashes"
down_revision: Union[str, None] = "012_create_hashes_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    exists = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'hashes' AND column_name = 'used_by')"
    )).scalar()
    if not exists:
        op.add_column("hashes", sa.Column("used_by", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("hashes", "used_by")
