"""Sensor inventory, desired/reported state, and maintenance history.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep iot_device as the single vehicle gateway; it drives existing fleet
    # status joins. A vehicle can have many managed sensors behind that gateway.
    op.execute("""
        CREATE TABLE managed_sensor (
            tenant_id text NOT NULL,
            vehicle_id text NOT NULL,
            device_id text NOT NULL CHECK (device_id <> ''),
            device_type text NOT NULL CHECK
                (device_type IN ('microphone', 'camera', 'lidar', 'gnss')),
            model text NOT NULL CHECK (model <> ''),
            desired_config jsonb NOT NULL DEFAULT '{}',
            reported_config jsonb NOT NULL DEFAULT '{}',
            config_version integer NOT NULL DEFAULT 1,
            reported_version integer NOT NULL DEFAULT 0,
            last_heartbeat_at timestamptz,
            health text NOT NULL DEFAULT 'OK' CHECK (health IN ('OK', 'FAULT')),
            network_info jsonb NOT NULL DEFAULT '{}',
            firmware_version text NOT NULL DEFAULT 'demo-1.0.0',
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, device_id),
            UNIQUE (tenant_id, vehicle_id, device_id),
            FOREIGN KEY (tenant_id, vehicle_id) REFERENCES vehicle
        )
    """)
    op.execute("""
        CREATE TABLE sensor_command (
            command_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id text NOT NULL,
            vehicle_id text NOT NULL,
            device_id text NOT NULL,
            command text NOT NULL CHECK
                (command IN ('reboot', 'microphone_test', 'diagnostics', 'firmware_update')),
            parameters jsonb NOT NULL DEFAULT '{}',
            status text NOT NULL DEFAULT 'SENT' CHECK
                (status IN ('SENT', 'RUNNING', 'COMPLETED', 'REJECTED', 'EXPIRED')),
            result jsonb,
            sent_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz,
            completed_at timestamptz,
            expires_at timestamptz NOT NULL DEFAULT now() + interval '60 seconds',
            FOREIGN KEY (tenant_id, vehicle_id, device_id)
                REFERENCES managed_sensor (tenant_id, vehicle_id, device_id)
        )
    """)
    op.execute(
        "CREATE INDEX sensor_command_pending ON sensor_command (tenant_id, status, sent_at)"
    )
    for table in ("managed_sensor", "sensor_command"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table}"
            " USING (tenant_id = current_setting('app.tenant_id', true))"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO av_app")


def downgrade() -> None:
    op.execute("DROP TABLE sensor_command, managed_sensor")
