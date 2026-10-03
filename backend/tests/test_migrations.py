"""Схема базы меняется только миграциями: модели и app/migrations должны совпадать."""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app import db
from app.db import Base, alembic_config


async def test_migrations_match_models(app_env):
    # app_env уже применил все миграции к чистой базе
    async with db.session_factory()() as s:
        conn = await s.connection()
        diff = await conn.run_sync(lambda c: compare_metadata(MigrationContext.configure(c), Base.metadata))
    assert diff == [], f"Модели изменились без миграции — добавьте файл в app/migrations/versions: {diff}"


def test_database_created_before_migrations_is_upgraded(tmp_path):
    """База, которую раньше создавал create_all (без alembic_version), получает новые колонки."""
    eng = create_engine(f"sqlite:///{tmp_path}/legacy.db")
    with eng.begin() as conn:
        command.upgrade(alembic_config(conn), "0001")
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(text("INSERT INTO users (github_id, login, balance_micro, created_at) VALUES (1, 'a', 5, '2026-01-01')"))
    with eng.begin() as conn:
        db._upgrade(conn)
    with eng.connect() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("agent_sessions")}
        assert "start_sha" in cols
        assert conn.execute(text("SELECT balance_micro FROM users")).scalar() == 5  # данные на месте
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0002"
