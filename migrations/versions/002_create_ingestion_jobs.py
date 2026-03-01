"""Create ingestion_jobs table for task status (FLOW-02).

Revision ID: 002_ingestion_jobs
Revises: 001_docs_chunks
Create Date: 2025-02-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002_ingestion_jobs"
down_revision: Union[str, None] = "001_docs_chunks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    r = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'ingestion_jobs')"
    )).scalar()
    if not r:
        op.execute("""
            CREATE TABLE ingestion_jobs (
                job_id VARCHAR(36) PRIMARY KEY,
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                document_id VARCHAR(36),
                error_message TEXT,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
            )
        """)


def downgrade() -> None:
    op.drop_table("ingestion_jobs")
