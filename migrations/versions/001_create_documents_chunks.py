"""Create documents and chunks tables for FLOW-02 ingestion.

Revision ID: 001_docs_chunks
Revises:
Create Date: 2025-02-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001_docs_chunks"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    r = conn.execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'document_status')")).scalar()
    if not r:
        op.execute("CREATE TYPE document_status AS ENUM ('pending', 'indexed', 'indexing_failed', 'superseded')")
    r = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'documents')"
    )).scalar()
    if not r:
        op.execute("""
            CREATE TABLE documents (
                id UUID PRIMARY KEY,
                external_id VARCHAR(255),
                owner_id VARCHAR(255) NOT NULL,
                filename VARCHAR(512) NOT NULL,
                content_type VARCHAR(128) NOT NULL,
                content TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                status document_status NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                error_message TEXT
            )
        """)
    for idx, cols in [
        ("ix_documents_external_id", "external_id"),
        ("ix_documents_owner_id", "owner_id"),
        ("ix_documents_owner_external", "owner_id, external_id"),
    ]:
        exists = conn.execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = :n)"), {"n": idx}).scalar()
        if not exists:
            op.execute(f"CREATE INDEX {idx} ON documents ({cols})")
    r = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'chunks')"
    )).scalar()
    if not r:
        op.execute("""
            CREATE TABLE chunks (
                id UUID PRIMARY KEY,
                document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
            )
        """)
    exists = conn.execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_chunks_document_id')")).scalar()
    if not exists:
        op.execute("CREATE INDEX ix_chunks_document_id ON chunks (document_id)")


def downgrade() -> None:
    op.drop_table("chunks")
    op.drop_table("documents")
    op.execute("DROP TYPE IF EXISTS document_status")
