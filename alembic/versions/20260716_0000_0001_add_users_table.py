"""add_users_table

Revision ID: 0001
Revises:
Create Date: 2026-07-16 00:00:00

Creates the `users` table (mirrors app/models/user.py + BaseModel's shared
columns) and seeds the three demo accounts so a fresh Postgres deployment
behaves identically to the SQLite/create_all_tables() demo path.
"""
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Same demo password every DEMO_USERS entry used pre-migration ("password123").
_DEMO_PASSWORD_HASH = "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW"


def upgrade() -> None:
    users_table = op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, index=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    now = datetime.now(timezone.utc)
    op.bulk_insert(
        users_table,
        [
            {
                "id": uuid.uuid4(),
                "created_at": now,
                "updated_at": now,
                "is_deleted": False,
                "deleted_at": None,
                "username": username,
                "hashed_password": _DEMO_PASSWORD_HASH,
                "role": role,
                "last_login_at": None,
            }
            for username, role in [
                ("admin_user", "Admin"),
                ("sales_user", "Sales"),
                ("operator_user", "Operator"),
            ]
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
