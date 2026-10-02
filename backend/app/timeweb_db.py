"""Управляемые БД Timeweb, к которым у сервиса есть пароль: созданные агентом или добавленные пользователем
в Настройках. Генерация доступов, хранение и строка подключения.

Пароль генерирует сервер и хранит зашифрованным; в контекст LLM он не попадает.
"""

from urllib.parse import quote

from sqlalchemy import select

from .db import session_factory
from .models import TimewebDatabase
from .security import decrypt, encrypt
from .timeweb import SCHEMES, db_family, db_public_ip

PRIVILEGES = {
    "postgres": ["SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "TRUNCATE", "REFERENCES", "TRIGGER", "TEMPORARY"],
    "mysql": [
        "ALTER", "CREATE", "CREATE_VIEW", "DELETE", "DROP", "INDEX", "INSERT", "LOCK_TABLES", "REFERENCES",
        "SELECT", "SHOW_VIEW", "TRIGGER", "UPDATE", "CREATE_TEMPORARY_TABLES",
    ],
}


async def save(user_id: int, cluster_id: int, name: str, db_type: str, db_name: str, login: str, password: str) -> None:
    async with session_factory()() as db:
        db.add(
            TimewebDatabase(
                user_id=user_id,
                cluster_id=int(cluster_id),
                name=name,
                db_type=db_type,
                db_name=db_name,
                login=login,
                password_enc=encrypt(password),
            )
        )
        await db.commit()


async def upsert(user_id: int, cluster_id: int, name: str, db_type: str, db_name: str, login: str, password: str) -> None:
    """Добавить или обновить базу, которую пользователь создал сам в панели Timeweb."""
    async with session_factory()() as db:
        rec = (
            await db.execute(
                select(TimewebDatabase).where(
                    TimewebDatabase.user_id == user_id, TimewebDatabase.cluster_id == int(cluster_id)
                )
            )
        ).scalar_one_or_none()
        if rec is None:
            rec = TimewebDatabase(user_id=user_id, cluster_id=int(cluster_id))
            db.add(rec)
        rec.name, rec.db_type, rec.db_name, rec.login = name, db_type, db_name, login
        rec.password_enc = encrypt(password)
        await db.commit()


async def delete(user_id: int, cluster_id: int) -> bool:
    async with session_factory()() as db:
        rec = (
            await db.execute(
                select(TimewebDatabase).where(
                    TimewebDatabase.user_id == user_id, TimewebDatabase.cluster_id == int(cluster_id)
                )
            )
        ).scalar_one_or_none()
        if not rec:
            return False
        await db.delete(rec)
        await db.commit()
        return True


async def get_record(user_id: int, cluster_id: int) -> TimewebDatabase | None:
    async with session_factory()() as db:
        return (
            await db.execute(
                select(TimewebDatabase).where(
                    TimewebDatabase.user_id == user_id, TimewebDatabase.cluster_id == int(cluster_id)
                )
            )
        ).scalar_one_or_none()


async def list_records(user_id: int) -> list[TimewebDatabase]:
    async with session_factory()() as db:
        return list(
            (
                await db.execute(
                    select(TimewebDatabase)
                    .where(TimewebDatabase.user_id == user_id)
                    .order_by(TimewebDatabase.id.desc())
                )
            ).scalars()
        )


class NotReady(Exception):
    pass


def connection(rec: TimewebDatabase, live: dict, url_scheme: str | None = None) -> dict:
    """Параметры подключения из записи и актуального состояния кластера. Бросает NotReady."""
    ip = db_public_ip(live)
    status = live.get("status")
    if not ip:
        raise NotReady(
            f"У базы «{rec.name}» пока нет публичного IP (статус: {status}). Дождитесь запуска; если IP так и не "
            "появится — подключите публичный IP базе в панели Timeweb."
        )
    password = decrypt(rec.password_enc) or ""
    port = int(live.get("port") or 5432)
    scheme = url_scheme or SCHEMES.get(db_family(rec.db_type), db_family(rec.db_type))
    family = db_family(rec.db_type)
    if family in ("redis", "valkey"):
        url = f"{scheme}://{quote(rec.login)}:{quote(password)}@{ip}:{port}/0"
    else:
        url = f"{scheme}://{quote(rec.login)}:{quote(password)}@{ip}:{port}/{quote(rec.db_name)}"
    return {"host": ip, "port": port, "name": rec.db_name, "user": rec.login, "password": password, "url": url}


def envs_for(conn: dict, mode: str, env_name: str, prefix: str) -> dict[str, str]:
    if mode == "parts":
        p = prefix or "DB_"
        return {
            f"{p}HOST": conn["host"],
            f"{p}PORT": str(conn["port"]),
            f"{p}NAME": conn["name"],
            f"{p}USER": conn["user"],
            f"{p}PASSWORD": conn["password"],
        }
    return {env_name or "DATABASE_URL": conn["url"]}
