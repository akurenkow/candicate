"""Add metadata JSONB to chunks for section_kind, company, date_range, project.

Revision ID: 003_chunks_metadata
Revises: 002_ingestion_jobs
Create Date: 2025-02-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "003_chunks_metadata"
down_revision: Union[str, None] = "002_ingestion_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    r = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'chunks' AND column_name = 'metadata')"
    )).scalar()
    if not r:
        op.add_column(
            "chunks",
            sa.Column("metadata", postgresql.JSONB(), nullable=True),
        )
        op.execute("UPDATE chunks SET metadata = '{}' WHERE metadata IS NULL")
        op.alter_column(
            "chunks",
            "metadata",
            nullable=False,
            server_default=sa.text("'{}'"),
        )


def downgrade() -> None:
    op.drop_column("chunks", "metadata")
