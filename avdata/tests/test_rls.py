"""Row-level security: one tenant can never read or change another tenant's rows.

Both tenants have a vehicle called v1, so isolation can't come from vehicle
IDs alone. Tenant B also has v2, which tenant A must never be able to reach.
"""

from collections.abc import Callable

import anyio
import psycopg
import pytest
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from avdata.postgres import tenant_transaction

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"

# Rows per table: (total seeded, tenant A's share).
SEEDED = {
    "tenant": (2, 1),
    "vehicle": (3, 1),
    "iot_device": (2, 1),
    "vehicle_command": (2, 1),
}
TABLES = list(SEEDED)


def as_tenant(conn: psycopg.Connection, tenant_id: str) -> None:
    """Set the tenant for the connection's current transaction only."""
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))


def count(conn: psycopg.Connection, table: str) -> int:
    query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
    return conn.execute(query).fetchone()[0]


@pytest.fixture(scope="module", autouse=True)
def seeded(migrated_db: None, pg_conninfo: Callable[[str], str]) -> None:
    """Seed both tenants as the superuser, which bypasses RLS."""
    with psycopg.connect(pg_conninfo("postgres")) as conn:
        params = {"a": TENANT_A, "b": TENANT_B}
        conn.execute(
            "INSERT INTO tenant (tenant_id, name)"
            " VALUES (%(a)s, 'Fleet A'), (%(b)s, 'Fleet B')",
            params,
        )
        conn.execute(
            "INSERT INTO vehicle (tenant_id, vehicle_id, name) VALUES"
            " (%(a)s, 'v1', 'A car'), (%(b)s, 'v1', 'B car'), (%(b)s, 'v2', 'B truck')",
            params,
        )
        conn.execute(
            "INSERT INTO iot_device (device_id, tenant_id, vehicle_id, thing_name) VALUES"
            " ('dev-a1', %(a)s, 'v1', 'thing-a1'), ('dev-b1', %(b)s, 'v1', 'thing-b1')",
            params,
        )
        conn.execute(
            "INSERT INTO vehicle_command (tenant_id, vehicle_id, command) VALUES"
            " (%(a)s, 'v1', 'SLOW_DOWN'), (%(b)s, 'v1', 'PULL_OVER')",
            params,
        )


@pytest.fixture
def app_conn(pg_conninfo: Callable[[str], str]) -> psycopg.Connection:
    """A connection as av_app. Everything it does is rolled back afterwards."""
    with psycopg.connect(pg_conninfo("av_app")) as conn:
        yield conn
        conn.rollback()


# --- Preconditions: without these, the other tests prove nothing ---


def test_app_roles_cannot_bypass_rls(app_conn):
    rows = app_conn.execute(
        "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles"
        " WHERE rolname IN ('av_app', 'av_owner') ORDER BY rolname"
    ).fetchall()
    assert rows == [("av_app", False, False), ("av_owner", False, False)]


@pytest.mark.parametrize("table", TABLES)
def test_superuser_sees_all_seeded_rows(pg_conninfo, table):
    """Positive control: zero rows below means blocked, not missing."""
    with psycopg.connect(pg_conninfo("postgres")) as conn:
        assert count(conn, table) == SEEDED[table][0]


def test_every_tenant_table_has_forced_rls_policy(app_conn):
    """Catches a future table with a tenant_id column but no RLS."""
    rows = app_conn.execute("""
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
               (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid)
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id'
        WHERE n.nspname = 'public' AND c.relkind = 'r'
        ORDER BY c.relname
    """).fetchall()
    assert {name for name, *_ in rows} == set(TABLES)
    for name, enabled, forced, policies in rows:
        assert (enabled, forced, policies >= 1) == (True, True, True), name


# --- Reads ---


@pytest.mark.parametrize("table", TABLES)
def test_tenant_sees_only_its_own_rows(app_conn, table):
    as_tenant(app_conn, TENANT_A)
    query = sql.SQL("SELECT tenant_id, count(*) FROM {} GROUP BY tenant_id").format(
        sql.Identifier(table)
    )
    assert app_conn.execute(query).fetchall() == [(TENANT_A, SEEDED[table][1])]


def test_other_tenants_row_is_invisible_even_by_exact_key(app_conn):
    as_tenant(app_conn, TENANT_A)
    rows = app_conn.execute(
        "SELECT * FROM vehicle WHERE tenant_id = %s AND vehicle_id IN ('v1', 'v2')",
        (TENANT_B,),
    ).fetchall()
    assert rows == []


@pytest.mark.parametrize("table", TABLES)
def test_no_tenant_set_sees_no_rows(app_conn, table):
    assert count(app_conn, table) == 0


def test_tenant_is_cleared_when_transaction_ends(app_conn):
    as_tenant(app_conn, TENANT_A)
    assert count(app_conn, "vehicle") == 1
    app_conn.rollback()

    # After a transaction-local setting ends, Postgres returns '' rather than NULL.
    setting = app_conn.execute("SELECT current_setting('app.tenant_id', true)")
    assert setting.fetchone()[0] == ""
    assert count(app_conn, "vehicle") == 0


def test_force_rls_applies_to_table_owner(pg_conninfo):
    with psycopg.connect(pg_conninfo("av_owner")) as conn:
        assert count(conn, "vehicle") == 0
        as_tenant(conn, TENANT_A)
        assert count(conn, "vehicle") == 1


# --- Writes ---


def test_cannot_insert_row_for_other_tenant(app_conn):
    as_tenant(app_conn, TENANT_A)
    with pytest.raises(
        psycopg.errors.InsufficientPrivilege, match="row-level security"
    ):
        app_conn.execute(
            "INSERT INTO vehicle (tenant_id, vehicle_id, name) VALUES (%s, 'v9', 'x')",
            (TENANT_B,),
        )


def test_update_cannot_reach_other_tenants_rows(app_conn):
    as_tenant(app_conn, TENANT_A)
    cur = app_conn.execute(
        "UPDATE vehicle SET name = 'hijacked' WHERE tenant_id = %s", (TENANT_B,)
    )
    assert cur.rowcount == 0
    # An unfiltered update touches only tenant A's one vehicle.
    assert app_conn.execute("UPDATE vehicle SET name = 'renamed'").rowcount == 1


def test_cannot_move_row_to_other_tenant(app_conn):
    as_tenant(app_conn, TENANT_A)
    with pytest.raises(
        psycopg.errors.InsufficientPrivilege, match="row-level security"
    ):
        app_conn.execute("UPDATE vehicle_command SET tenant_id = %s", (TENANT_B,))


def test_app_cannot_delete(app_conn):
    as_tenant(app_conn, TENANT_A)
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied"):
        app_conn.execute("DELETE FROM vehicle_command")


def test_composite_fk_blocks_link_to_other_tenants_vehicle(app_conn):
    """FK checks bypass RLS, so the composite FK is what stops this."""
    as_tenant(app_conn, TENANT_A)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        app_conn.execute(
            "INSERT INTO vehicle_command (tenant_id, vehicle_id, command)"
            " VALUES (%s, 'v2', 'PULL_OVER')",
            (TENANT_A,),
        )


# --- Pooled connections, through avdata.postgres.tenant_transaction ---


async def vehicle_tenants(conn: AsyncConnection) -> set[str]:
    result = await conn.execute(text("SELECT DISTINCT tenant_id FROM vehicle"))
    return set(result.scalars())


@pytest.fixture
async def single_connection_engine(pg_url):
    """An engine whose pool holds one connection, so every use reuses it."""
    engine = create_async_engine(pg_url("av_app"), pool_size=1, max_overflow=0)
    yield engine
    await engine.dispose()


@pytest.mark.anyio
async def test_tenant_does_not_leak_to_next_pool_user(single_connection_engine):
    engine = single_connection_engine
    async with tenant_transaction(engine, TENANT_A) as conn:
        assert await vehicle_tenants(conn) == {TENANT_A}
        pid = await conn.scalar(text("SELECT pg_backend_pid()"))

    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT pg_backend_pid()")) == pid
        assert await vehicle_tenants(conn) == set()

    async with tenant_transaction(engine, TENANT_B) as conn:
        assert await vehicle_tenants(conn) == {TENANT_B}


@pytest.mark.anyio
async def test_tenant_does_not_leak_after_error(single_connection_engine):
    engine = single_connection_engine
    with pytest.raises(RuntimeError):
        async with tenant_transaction(engine, TENANT_A):
            raise RuntimeError("request failed mid-transaction")

    async with engine.connect() as conn:
        assert await vehicle_tenants(conn) == set()


@pytest.mark.anyio
async def test_empty_tenant_is_rejected(single_connection_engine):
    with pytest.raises(ValueError):
        async with tenant_transaction(single_connection_engine, ""):
            pass


@pytest.mark.anyio
async def test_concurrent_tenants_stay_isolated(pg_url):
    """20 interleaved transactions share 2 pooled connections."""
    engine = create_async_engine(pg_url("av_app"), pool_size=2, max_overflow=0)
    results = []

    async def query_as(tenant_id: str) -> None:
        async with tenant_transaction(engine, tenant_id) as conn:
            await conn.execute(text("SELECT pg_sleep(0.01)"))
            pid = await conn.scalar(text("SELECT pg_backend_pid()"))
            results.append((tenant_id, await vehicle_tenants(conn), pid))

    try:
        async with anyio.create_task_group() as tg:
            for i in range(20):
                tg.start_soon(query_as, TENANT_A if i % 2 == 0 else TENANT_B)
    finally:
        await engine.dispose()

    assert len(results) == 20
    assert len({pid for *_, pid in results}) <= 2
    for tenant_id, seen, _ in results:
        assert seen == {tenant_id}
