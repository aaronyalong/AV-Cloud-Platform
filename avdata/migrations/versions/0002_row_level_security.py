"""Row-level security on every tenant table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-08

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("tenant", "vehicle", "iot_device", "vehicle_command")


def upgrade() -> None:
    """Upgrade schema."""
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE applies the policy to the table owner (av_owner) too.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # With no WITH CHECK clause, USING also checks inserted and updated
        # rows. An unset tenant gives NULL, which matches no rows.
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table}"
            " USING (tenant_id = current_setting('app.tenant_id', true))"
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
