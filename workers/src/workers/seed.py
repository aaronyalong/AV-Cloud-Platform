"""Seed the demo tenants, vehicles, and IoT devices. Safe to run repeatedly.

    uv run --env-file .env python -m workers.seed

Runs as av_owner, because av_app can't create tenants. Row-level security
still applies (FORCE), so each tenant's rows are written inside that
tenant's transaction.
"""

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from avdata.postgres import tenant_transaction

# tenant_id -> (tenant name, [(vehicle_id, vehicle name), ...])
DEMO_FLEET: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "t-acme": (
        "Acme Logistics",
        [
            ("veh-07", "Delivery van 07"),
            ("veh-09", "Box truck 09"),
            ("veh-12", "Shuttle 12"),
        ],
    ),
    "t-beta": ("Beta Mobility", [("veh-01", "Robotaxi 01")]),
}


def device_id(vehicle_id: str) -> str:
    return f"dev-{vehicle_id}"


async def seed_demo_fleet(owner_engine: AsyncEngine) -> None:
    for tenant_id, (tenant_name, vehicles) in DEMO_FLEET.items():
        async with tenant_transaction(owner_engine, tenant_id) as conn:
            await conn.execute(
                text(
                    "INSERT INTO tenant (tenant_id, name) VALUES (:t, :name)"
                    " ON CONFLICT DO NOTHING"
                ),
                {"t": tenant_id, "name": tenant_name},
            )
            for vehicle_id, name in vehicles:
                params = {"t": tenant_id, "v": vehicle_id, "name": name}
                await conn.execute(
                    text(
                        "INSERT INTO vehicle (tenant_id, vehicle_id, name)"
                        " VALUES (:t, :v, :name) ON CONFLICT DO NOTHING"
                    ),
                    params,
                )
                await conn.execute(
                    text(
                        "INSERT INTO iot_device (device_id, tenant_id, vehicle_id, thing_name)"
                        " VALUES (:d, :t, :v, :thing) ON CONFLICT DO NOTHING"
                    ),
                    {
                        **params,
                        "d": device_id(vehicle_id),
                        "thing": f"{tenant_id}-{vehicle_id}",
                    },
                )


async def main() -> None:
    url = os.environ.get("OWNER_DATABASE_URL")
    if not url:
        raise SystemExit("OWNER_DATABASE_URL is not set. Run with --env-file .env")
    engine = create_async_engine(url)
    try:
        await seed_demo_fleet(engine)
    finally:
        await engine.dispose()
    for tenant_id, (_, vehicles) in DEMO_FLEET.items():
        print(f"{tenant_id}: {', '.join(v for v, _ in vehicles)}")


if __name__ == "__main__":
    asyncio.run(main())
