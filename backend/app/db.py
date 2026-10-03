import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(url: str | None = None) -> AsyncEngine:
    global _engine, _sessionmaker
    url = url or get_settings().database_url
    if url.startswith("sqlite") and ":///./" in url:
        os.makedirs(os.path.dirname(url.split(":///", 1)[1]) or ".", exist_ok=True)
    _engine = create_async_engine(url, pool_pre_ping=True)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    return _sessionmaker


MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "migrations")
# Первая миграция повторяет схему, которую раньше создавал create_all
BASELINE_REVISION = "0001"


def alembic_config(connection=None):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS_DIR)
    cfg.attributes["connection"] = connection
    return cfg


def _upgrade(connection) -> None:
    from alembic import command
    from sqlalchemy import inspect

    cfg = alembic_config(connection)
    tables = inspect(connection).get_table_names()
    if "alembic_version" not in tables and "agent_sessions" in tables:
        # База создана до перехода на миграции: схема уже как в базовой миграции
        command.stamp(cfg, BASELINE_REVISION)
    command.upgrade(cfg, "head")


async def migrate() -> None:
    """Привести схему базы к актуальной: применить новые миграции из app/migrations/versions."""
    if _engine is None:
        init_engine()
    assert _engine is not None
    async with _engine.begin() as conn:
        await conn.run_sync(_upgrade)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with session_factory()() as session:
        yield session


async def dispose() -> None:
    if _engine is not None:
        await _engine.dispose()
