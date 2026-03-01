"""Add working_query and detected_lang to rag_messages (FLOW-01 lang/translate).

Revision ID: 006_rag_messages_working_query
Revises: 005_rag_messages
Create Date: 2026-02-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006_rag_messages_working_query"
down_revision: Union[str, None] = "005_rag_messages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    for col, col_type, nullable in [
        ("working_query", sa.Text(), True),
        ("detected_lang", sa.String(10), True),
    ]:
        r = conn.execute(sa.text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'rag_messages' AND column_name = :c)"
        ), {"c": col}).scalar()
        if not r:
            op.add_column("rag_messages", sa.Column(col, col_type, nullable=nullable))


def downgrade() -> None:
    op.drop_column("rag_messages", "detected_lang")
    op.drop_column("rag_messages", "working_query")
