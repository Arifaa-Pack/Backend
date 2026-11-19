"""One async engine per logical database; sessions with explicit transactions.

Each package uses ONLY its own database:  kx.crm -> CRM, kx.automation -> AUTOMATION, kx.agents -> AGENTS.
Cross-database work goes through service calls or events (see kx.core.events), never SQL joins. Each database
has
its own DSN, so any one can move to its own server by changing that DSN (D48, docs/DATABASE.md).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import orjson
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from kx.core.errors import NotFound
from kx.core.settings import CoreSettings, LogicalDb

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def make_base(schema: str) -> type[DeclarativeBase]:
    """Declarative base bound to one Postgres schema (crm / automation / agents)."""

    class Base(DeclarativeBase):
        metadata = MetaData(schema=schema, naming_convention=NAMING)

    return Base


def _json_dumps(value: object) -> str:
    return orjson.dumps(value, option=orjson.OPT_NON_STR_KEYS).decode()


def connect_args(settings: CoreSettings, db: LogicalDb) -> dict[str, Any]:
    """asyncpg connect arguments. Direct to Postgres: per-connection `search_path` and `statement_timeout`.
    Through PgBouncer in transaction mode (D48): those startup parameters are not passed on, and server-side
    prepared statements would land on another backend, so both caches are off and every statement gets a
    unique name; `search_path` / `statement_timeout` come from `ALTER ROLE … SET` (db_bootstrap.sh)."""
    application = f"kx-{settings.company_code}-{db.value}"
    if settings.db_pgbouncer:
        return {
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__kx_{uuid.uuid4().hex}__",
            "server_settings": {"application_name": application},
        }
    return {
        "server_settings": {
            "statement_timeout": str(settings.db_statement_timeout_ms),
            "application_name": application,
            "search_path": f"{db.value},public",
        }
    }


class Databases:
    """Owns the engines. Create once per process (FastAPI lifespan / worker startup)."""

    def __init__(self, settings: CoreSettings, only: set[LogicalDb] | None = None) -> None:
        self._engines: dict[LogicalDb, AsyncEngine] = {}
        self._makers: dict[LogicalDb, async_sessionmaker[AsyncSession]] = {}
        for db in only or set(LogicalDb):
            engine = create_async_engine(
                settings.dsn_for(db),
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
                pool_pre_ping=True,
                json_serializer=_json_dumps,
                json_deserializer=orjson.loads,
                connect_args=connect_args(settings, db),
            )
            self._engines[db] = engine
            self._makers[db] = async_sessionmaker(engine, expire_on_commit=False)

    def engine(self, db: LogicalDb) -> AsyncEngine:
        return self._engines[db]

    @asynccontextmanager
    async def transaction(self, db: LogicalDb) -> AsyncIterator[AsyncSession]:
        """Session inside one transaction: commit on success, rollback on error."""
        async with self._makers[db]() as session, session.begin():
            yield session

    def dependency(self, db: LogicalDb) -> Callable[[], AsyncIterator[AsyncSession]]:
        """FastAPI dependency yielding a transactional session for `db`."""

        async def _dep() -> AsyncIterator[AsyncSession]:
            async with self.transaction(db) as session:
                yield session

        return _dep

    async def dispose(self) -> None:
        for engine in self._engines.values():
            await engine.dispose()


async def get_or_404[M](
    session: AsyncSession, model: type[M], key: Any, message: str, *, lock: bool = False
) -> M:
    """The row by primary key, or NotFound(message) (review §3.2: ~25 places wrote these three lines; the
    message is passed whole, so every problem+json answer stays word for word the same)."""
    row = await session.get(model, key, with_for_update=lock)
    if row is None:
        raise NotFound(message)
    return row
