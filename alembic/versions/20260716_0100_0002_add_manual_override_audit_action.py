"""add_manual_override_audit_action

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-16 01:00:00

Adds MANUAL_OVERRIDE to the audit_action_type_enum Postgres enum, backing the
new "Sales/Admin manually assigns priority to an UNASSIGNED lead" feature.
SQLite has no native enum type (values are unconstrained), so this migration
is a no-op there — only Postgres needs the ALTER TYPE.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE audit_action_type_enum ADD VALUE IF NOT EXISTS 'MANUAL_OVERRIDE'")


def downgrade() -> None:
    # Postgres cannot drop a single enum value without recreating the type;
    # left as a no-op (matches the project's other enum migrations' approach).
    pass
