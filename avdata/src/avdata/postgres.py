"""PostgreSQL access scoped to one tenant through row-level security."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


@asynccontextmanager
async def tenant_transaction(
    engine: AsyncEngine, tenant_id: str
) -> AsyncIterator[AsyncConnection]:
    """Open a transaction that can only read and write tenant_id's rows.

    The tenant is set with set_config(..., true), which lasts only until the
    transaction ends, so it never carries over to the next user of a pooled
    connection. The transaction commits on success and rolls back on error.
    """
    if not tenant_id:
        raise ValueError("tenant_id is required")
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        yield conn
