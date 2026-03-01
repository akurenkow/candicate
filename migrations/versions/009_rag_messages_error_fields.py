"""Add error_message and failed_service to rag_messages (escalated/failed tracking).

Revision ID: 009_rag_messages_error_fields
Revises: 008_rag_messages_owner_id
Create Date: 2026-02-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "009_rag_messages_error_fields"
down_revision: Union[str, None] = "008_rag_messages_owner_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    for col in ("error_message", "failed_service"):
        r = conn.execute(sa.text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'rag_messages' AND column_name = :c)"
        ), {"c": col}).scalar()
        if not r:
            if col == "error_message":
                op.add_column("rag_messages", sa.Column(col, sa.Text(), nullable=True))
            else:
                op.add_column("rag_messages", sa.Column(col, sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("rag_messages", "failed_service")
    op.drop_column("rag_messages", "error_message")
