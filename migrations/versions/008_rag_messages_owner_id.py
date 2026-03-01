"""Add owner_id to rag_messages (FLOW-01 candidate scope).

Revision ID: 008_rag_messages_owner_id
Revises: 007_assistant_translated
Create Date: 2026-02-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "008_rag_messages_owner_id"
down_revision: Union[str, None] = "007_assistant_translated"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    r = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'rag_messages' AND column_name = 'owner_id')"
    )).scalar()
    if not r:
        op.add_column("rag_messages", sa.Column("owner_id", sa.String(255), nullable=False, server_default=""))
        op.create_index("ix_rag_messages_owner_id", "rag_messages", ["owner_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_rag_messages_owner_id", table_name="rag_messages")
    op.drop_column("rag_messages", "owner_id")
