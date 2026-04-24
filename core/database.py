from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from core.config import settings

engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# Columns that may be missing from databases created before these fields were added.
# Each entry: (table, column, DDL type)
_MIGRATIONS = [
    ("scan_results", "registration_date",  "VARCHAR(32)"),
    ("scan_results", "registrar",           "VARCHAR(255)"),
    ("scan_results", "registrant_owner",    "VARCHAR(255)"),
]


async def _run_migrations(conn) -> None:
    """
    Add any columns that are missing from an existing schema.
    Safe to run on every startup — uses IF NOT EXISTS (PostgreSQL 9.6+).
    """
    for table, column, col_type in _MIGRATIONS:
        await conn.execute(text(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}"
        ))


async def init_db() -> None:
    """Create all tables and apply any pending column migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _run_migrations(conn)


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
