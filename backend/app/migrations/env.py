"""Окружение Alembic. Миграции запускаются из приложения (app.db.migrate) на уже открытом соединении."""

from alembic import context

from app import models  # noqa: F401  регистрирует таблицы
from app.db import Base

config = context.config
connection = config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Миграции запускаются через app.db.migrate(), а не напрямую командой alembic")

context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    # SQLite не умеет большинство ALTER TABLE: Alembic пересоздаёт таблицу целиком
    render_as_batch=connection.dialect.name == "sqlite",
    compare_type=True,
)
with context.begin_transaction():
    context.run_migrations()
