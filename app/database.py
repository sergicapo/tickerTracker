"""
SQLAlchemy async engine and session management.
Uses aiosqlite as the async driver for SQLite.
"""
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base, Ticker
from app.sources import DEFAULT_DATA_SOURCE

logger = logging.getLogger(__name__)

# Read DATABASE_URL from environment; convert sqlite:/// → sqlite+aiosqlite:///
_raw_url: str = os.getenv("DATABASE_URL", "sqlite:////data/ticker_tracker.db")

if _raw_url.startswith("sqlite:///") and not _raw_url.startswith("sqlite+aiosqlite"):
    DATABASE_URL = _raw_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
else:
    DATABASE_URL = _raw_url

logger.debug("Using database URL: %s", DATABASE_URL)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def init_db() -> None:
    """Create all tables if they do not exist, then apply lightweight migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_schema(conn)
    logger.info("Database tables initialised.")


async def _migrate_schema(conn: AsyncConnection) -> None:
    """
    Add columns that `create_all` won't add to a pre-existing table.

    `Base.metadata.create_all` only creates missing tables — it never alters
    an existing one. Older deployments (e.g. a database volume created before
    the `data_source` / `source_config` columns existed) need those columns
    added explicitly, or every query against `tickers` fails with
    "no such column".
    """
    existing_columns = {
        col["name"] for col in await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns(Ticker.__tablename__)
        )
    }

    if "data_source" not in existing_columns:
        await conn.execute(
            text(
                "ALTER TABLE tickers ADD COLUMN data_source VARCHAR(32) "
                f"NOT NULL DEFAULT '{DEFAULT_DATA_SOURCE.value}'"
            )
        )
        logger.info("Migrated tickers table: added data_source column.")

    if "source_config" not in existing_columns:
        await conn.execute(text("ALTER TABLE tickers ADD COLUMN source_config JSON"))
        logger.info("Migrated tickers table: added source_config column.")


async def close_db() -> None:
    """Dispose the engine connection pool."""
    await engine.dispose()
    logger.info("Database connection pool disposed.")


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Context-manager that yields a database session and handles commit/rollback."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an AsyncSession.
    Commit is handled by the caller (or the CRUD layer).
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
