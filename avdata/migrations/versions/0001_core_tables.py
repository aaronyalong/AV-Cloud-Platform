"""Core DIM tables: tenant, vehicle, iot_device, vehicle_command.

Revision ID: 0001
Revises:
Create Date: 2026-11-08

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        CREATE TABLE tenant (
            tenant_id text PRIMARY KEY CHECK (tenant_id <> ''),
            name      text NOT NULL,
            status    text NOT NULL DEFAULT 'ACTIVE'
                      CHECK (status IN ('ACTIVE', 'SUSPENDED'))
        )
    """)
    op.execute("""
        CREATE TABLE vehicle (
            tenant_id     text NOT NULL REFERENCES tenant,
            vehicle_id    text NOT NULL CHECK (vehicle_id <> ''),
            name          text NOT NULL,
            owner_user_id text,
            last_seen_at  timestamptz,
            PRIMARY KEY (tenant_id, vehicle_id)
        )
    """)
    # Composite FKs include tenant_id so a row can't point at another
    # tenant's vehicle. FK checks bypass RLS, so RLS alone wouldn't stop it.
    op.execute("""
        CREATE TABLE iot_device (
            device_id          text PRIMARY KEY CHECK (device_id <> ''),
            tenant_id          text NOT NULL,
            vehicle_id         text NOT NULL,
            thing_name         text NOT NULL UNIQUE,
            last_heartbeat_at  timestamptz,
            last_disconnect_at timestamptz,
            UNIQUE (tenant_id, vehicle_id),
            FOREIGN KEY (tenant_id, vehicle_id) REFERENCES vehicle
        )
    """)
    op.execute("""
        CREATE TABLE vehicle_command (
            command_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id  text NOT NULL,
            vehicle_id text NOT NULL,
            alert_id   text,
            command    text NOT NULL CHECK (command IN ('SLOW_DOWN', 'PULL_OVER')),
            status     text NOT NULL DEFAULT 'SENT'
                       CHECK (status IN ('SENT', 'ACKED', 'REJECTED', 'EXPIRED')),
            sent_at    timestamptz NOT NULL DEFAULT now(),
            acked_at   timestamptz,
            FOREIGN KEY (tenant_id, vehicle_id) REFERENCES vehicle
        )
    """)
    op.execute(
        "CREATE INDEX vehicle_command_vehicle_idx"
        " ON vehicle_command (tenant_id, vehicle_id, sent_at DESC)"
    )

    # The app reads tenants but never creates or deletes rows.
    op.execute("GRANT SELECT ON tenant TO av_app")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON vehicle, iot_device, vehicle_command TO av_app"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE vehicle_command, iot_device, vehicle, tenant")
