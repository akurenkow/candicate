"""Add telegram bot fields to users and hashes tables.

Revision ID: 010_telegram_bot_schema
Revises: 009_rag_messages_error_fields
Create Date: 2026-02-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "010_telegram_bot_schema"
down_revision: Union[str, None] = "009_rag_messages_error_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    users_exists = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'users')"
    )).scalar()
    if users_exists:
        for col, col_type, extra in [
            ("telegram_id", sa.String(255), {"nullable": True}),
            ("dialog_owner_id", sa.String(255), {"nullable": True}),
            ("mode", None, {"nullable": True}),
            ("current_hash_uuid", postgresql.UUID(as_uuid=True), {"nullable": True}),
        ]:
            r = conn.execute(sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'users' AND column_name = :c)"
            ), {"c": col}).scalar()
            if not r:
                if col == "mode":
                    type_exists = conn.execute(sa.text(
                        "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_mode')"
                    )).scalar()
                    if not type_exists:
                        op.execute("CREATE TYPE user_mode AS ENUM ('candidate', 'hr')")
                    op.add_column("users", sa.Column(col, postgresql.ENUM("candidate", "hr", name="user_mode", create_type=False), nullable=True))
                else:
                    op.add_column("users", sa.Column(col, col_type, nullable=extra.get("nullable", True)))

        r = conn.execute(sa.text(
            "SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_telegram_id_key')"
        )).scalar()
        if not r:
            op.create_unique_constraint("users_telegram_id_key", "users", ["telegram_id"])

    hashes_exists = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'hashes')"
    )).scalar()
    if hashes_exists:
        r = conn.execute(sa.text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'hashes' AND column_name = 'expires_at')"
        )).scalar()
        if not r:
            op.add_column("hashes", sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    users_exists = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'users')"
    )).scalar()
    if users_exists:
        r = conn.execute(sa.text(
            "SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_telegram_id_key')"
        )).scalar()
        if r:
            op.drop_constraint("users_telegram_id_key", "users", type_="unique")
        for col in ("telegram_id", "dialog_owner_id", "mode", "current_hash_uuid"):
            r = conn.execute(sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'users' AND column_name = :c)"
            ), {"c": col}).scalar()
            if r:
                op.drop_column("users", col)
    hashes_exists = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'hashes')"
    )).scalar()
    if hashes_exists:
        r = conn.execute(sa.text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'hashes' AND column_name = 'expires_at')"
        )).scalar()
        if r:
            op.drop_column("hashes", "expires_at")
    op.execute("DROP TYPE IF EXISTS user_mode")
