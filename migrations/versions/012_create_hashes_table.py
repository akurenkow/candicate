"""Create hashes table for telegram bot /hash command.

Revision ID: 012_create_hashes_table
Revises: 011_create_users_table
Create Date: 2026-02-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "012_create_hashes_table"
down_revision: Union[str, None] = "011_create_users_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    exists = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'hashes')"
    )).scalar()
    if not exists:
        op.execute("""
            CREATE TABLE hashes (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                owner_id VARCHAR(255) NOT NULL,
                hash VARCHAR(512) NOT NULL,
                is_valid BOOLEAN NOT NULL DEFAULT true,
                expires_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
            )
        """)
        op.create_index("ix_hashes_owner_id", "hashes", ["owner_id"], unique=False)
        op.create_index("ix_hashes_hash", "hashes", ["hash"], unique=False)
        op.create_index("ix_hashes_expires_at", "hashes", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_hashes_expires_at", table_name="hashes")
    op.drop_index("ix_hashes_hash", table_name="hashes")
    op.drop_index("ix_hashes_owner_id", table_name="hashes")
    op.drop_table("hashes")
