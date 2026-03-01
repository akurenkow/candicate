"""Create users table if not exists for telegram bot.

Revision ID: 011_create_users_table
Revises: 010_telegram_bot_schema
Create Date: 2026-02-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "011_create_users_table"
down_revision: Union[str, None] = "010_telegram_bot_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    users_exists = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'users')"
    )).scalar()
    if not users_exists:
        type_exists = conn.execute(sa.text(
            "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_mode')"
        )).scalar()
        if not type_exists:
            op.execute("CREATE TYPE user_mode AS ENUM ('candidate', 'hr')")
        op.execute("""
            CREATE TABLE users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                telegram_id VARCHAR(255) UNIQUE,
                role VARCHAR(64),
                is_active BOOLEAN NOT NULL DEFAULT true,
                source VARCHAR(64),
                language VARCHAR(8),
                dialog_owner_id VARCHAR(255),
                mode user_mode,
                current_hash_uuid UUID
            )
        """)


def downgrade() -> None:
    conn = op.get_bind()
    users_exists = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'users')"
    )).scalar()
    if users_exists:
        op.drop_table("users")
