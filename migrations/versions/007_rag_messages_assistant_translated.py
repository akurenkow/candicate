"""Add assistant_text_translated for strict separation (FLOW-01 post-process).

Revision ID: 007_assistant_translated
Revises: 006_rag_messages_working_query
Create Date: 2026-02-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "007_assistant_translated"
down_revision: Union[str, None] = "006_rag_messages_working_query"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    r = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'rag_messages' AND column_name = 'assistant_text_translated')"
    )).scalar()
    if not r:
        op.add_column("rag_messages", sa.Column("assistant_text_translated", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("rag_messages", "assistant_text_translated")
