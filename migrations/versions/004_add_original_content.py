"""Add original_content TEXT to documents for storing untranslated resume.

Revision ID: 004_add_original_content
Revises: 003_chunks_metadata
Create Date: 2026-02-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004_add_original_content"
down_revision: Union[str, None] = "003_chunks_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    r = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'documents' AND column_name = 'original_content')"
    )).scalar()
    if not r:
        op.add_column(
            "documents",
            sa.Column("original_content", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("documents", "original_content")
