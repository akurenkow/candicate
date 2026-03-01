"""RAG message table for conversation-manager lifecycle (FLOW-01).

Revision ID: 005_rag_messages
Revises: 004_add_original_content
Create Date: 2026-02-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "005_rag_messages"
down_revision: Union[str, None] = "004_add_original_content"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    r = conn.execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'rag_message_status')")).scalar()
    if not r:
        op.execute(
            "CREATE TYPE rag_message_status AS ENUM ("
            "'queued', 'locked', 'processing', 'completed', 'escalated', 'failed')"
        )
    r = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'rag_messages')"
    )).scalar()
    if not r:
        op.execute("""
            CREATE TABLE rag_messages (
                id UUID PRIMARY KEY,
                user_raw_text TEXT NOT NULL,
                user_normalized_text TEXT,
                assistant_text TEXT,
                status rag_message_status NOT NULL DEFAULT 'queued',
                processing_lock VARCHAR(255),
                locked_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
            )
        """)
    for idx, cols in [
        ("ix_rag_messages_status", "status"),
        ("ix_rag_messages_processing_lock", "processing_lock"),
    ]:
        exists = conn.execute(sa.text(
            "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = :n)"
        ), {"n": idx}).scalar()
        if not exists:
            op.execute(f'CREATE INDEX {idx} ON rag_messages ({cols})')


def downgrade() -> None:
    op.drop_index("ix_rag_messages_processing_lock", table_name="rag_messages")
    op.drop_index("ix_rag_messages_status", table_name="rag_messages")
    op.drop_table("rag_messages")
    op.execute("DROP TYPE rag_message_status")
