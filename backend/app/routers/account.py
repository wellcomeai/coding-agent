from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import billing, timeweb_db
from ..config import get_settings
from ..db import get_db
from ..deps import admin_user, current_user
from ..models import LedgerEntry, User
from ..security import decrypt, encrypt
from ..timeweb import TimewebClient, TimewebError, db_public_ip

router = APIRouter(prefix="/api", tags=["account"])


class TimewebToken(BaseModel):
    token: str = Field(min_length=20, max_length=5000)


@router.put("/settings/timeweb")
async def set_timeweb_token(body: TimewebToken, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    token = body.token.strip()
    try:
        status = await TimewebClient(token).account_status()
    except TimewebError as e:
        raise HTTPException(400, f"Токен не принят Timeweb Cloud: {e}") from e
    user.timeweb_token_enc = encrypt(token)
    await db.commit()
    return {"ok": True, "login": status.get("login")}


@router.get("/settings/timeweb/status")
async def timeweb_status(user: User = Depends(current_user)):
    """Проверка интеграции: токен действителен и какие GitHub-аккаунты подключены в Timeweb."""
    token = decrypt(user.timeweb_token_enc)
    if not token:
        return {"connected": False}
    client = TimewebClient(token)
    try:
        account = await client.account_status()
    except TimewebError as e:
        return {"connected": True, "valid": False, "error": f"Токен не принят Timeweb ({e.status}). Создайте новый токен."}
    out: dict = {"connected": True, "valid": True, "account": account.get("login"), "providers": []}
    try:
        for p in await client.list_providers():
            item = {"login": p.get("login"), "type": str(p.get("provider_type") or p.get("provider") or "").lower(), "repos_count": None}
            try:
                item["repos_count"] = len(await client.list_provider_repos(p["provider_id"]))
            except TimewebError as e:
                item["error"] = str(e)
            out["providers"].append(item)
    except TimewebError as e:
        out["providers_error"] = f"Не удалось получить подключённые аккаунты GitHub: {e}"
    return out


@router.get("/timeweb/databases")
async def my_databases(user: User = Depends(current_user)):
    """Базы, к которым у сервиса есть пароль (добавлены в Настройках или созданы агентом), со статусом из Timeweb."""
    records = await timeweb_db.list_records(user.id)
    token = decrypt(user.timeweb_token_enc)
    client = TimewebClient(token) if token else None
    out = []
    for r in records:
        item = {"id": r.cluster_id, "name": r.name, "type": r.db_type, "db_name": r.db_name, "user": r.login,
                "created_at": r.created_at.isoformat(), "status": None, "host": None, "port": None}
        if client:
            try:
                live = await client.get_database(r.cluster_id)
                item.update(status=live.get("status"), host=db_public_ip(live), port=live.get("port"))
            except TimewebError as e:
                item["status"] = "deleted" if e.status == 404 else f"ошибка {e.status}"
        out.append(item)
    return {"databases": out}


def _tw_client(user: User) -> TimewebClient:
    token = decrypt(user.timeweb_token_enc)
    if not token:
        raise HTTPException(400, "Сначала подключите Timeweb Cloud")
    return TimewebClient(token)


@router.get("/timeweb/databases/available")
async def available_databases(user: User = Depends(current_user)):
    """Все базы аккаунта Timeweb — чтобы выбрать созданную вручную и добавить её пароль."""
    client = _tw_client(user)
    added = {r.cluster_id for r in await timeweb_db.list_records(user.id)}
    try:
        dbs = await client.list_databases()
    except TimewebError as e:
        raise HTTPException(502, str(e)) from e
    return {
        "databases": [
            {"id": d.get("id"), "name": d.get("name"), "type": d.get("type"), "status": d.get("status"),
             "location": d.get("location"), "public_ip": db_public_ip(d), "added": d.get("id") in added}
            for d in dbs
        ]
    }


class AddDatabase(BaseModel):
    cluster_id: int
    password: str = Field(min_length=1, max_length=500)
    login: str | None = Field(default=None, max_length=100)
    db_name: str | None = Field(default=None, max_length=255)


async def _check_postgres(host: str, port: int, login: str, password: str, db_name: str) -> str | None:
    """Проверить, что с этим паролем можно подключиться. None — всё хорошо, иначе текст ошибки."""
    import asyncpg

    try:
        conn = await asyncpg.connect(host=host, port=port, user=login, password=password, database=db_name, timeout=10)
    except Exception as e:  # noqa: BLE001
        return f"{e.__class__.__name__}: {e}"[:300]
    await conn.close()
    return None


@router.post("/timeweb/databases")
async def add_database(body: AddDatabase, user: User = Depends(current_user)):
    """Добавить базу, созданную вручную в панели Timeweb: агент сможет подключить её к приложению,
    а пароль не попадёт в чат."""
    client = _tw_client(user)
    try:
        live = await client.get_database(body.cluster_id)
        login = body.login or next((a.get("login") for a in await client.list_db_admins(body.cluster_id)), None)
        db_name = body.db_name or next((i.get("name") for i in await client.list_db_instances(body.cluster_id)), None)
    except TimewebError as e:
        raise HTTPException(404 if e.status == 404 else 502, f"База не найдена в вашем аккаунте Timeweb: {e}") from e
    if not login or not db_name:
        raise HTTPException(400, "Не удалось определить пользователя или имя базы — укажите их вручную")
    await timeweb_db.upsert(user.id, body.cluster_id, live.get("name") or str(body.cluster_id),
                            live.get("type") or "", db_name, login, body.password.strip())
    host, port = db_public_ip(live), int(live.get("port") or 5432)
    out = {"ok": True, "id": body.cluster_id, "name": live.get("name"), "user": login, "db_name": db_name,
           "public_ip": host, "check": None}
    if not host:
        out["warning"] = "У базы нет публичного IP — включите его в панели Timeweb, иначе приложение её не увидит."
    elif (live.get("type") or "").startswith("postgres") and live.get("status") == "started":
        if err := await _check_postgres(host, port, login, body.password.strip(), db_name):
            out["warning"] = f"Не удалось подключиться с этим паролем: {err}"
        else:
            out["check"] = "ok"
    return out


@router.delete("/timeweb/databases/{cluster_id}")
async def forget_database(cluster_id: int, user: User = Depends(current_user)):
    """Убрать базу из сервиса (забыть пароль). Сама база в Timeweb не удаляется."""
    if not await timeweb_db.delete(user.id, cluster_id):
        raise HTTPException(404, "База не найдена")
    return {"ok": True}


@router.post("/timeweb/databases/{cluster_id}/connection")
async def database_connection(cluster_id: int, user: User = Depends(current_user)):
    """Параметры подключения (с паролем) — только владельцу, по явному запросу из интерфейса."""
    rec = await timeweb_db.get_record(user.id, cluster_id)
    token = decrypt(user.timeweb_token_enc)
    if not rec or not token:
        raise HTTPException(404, "База не найдена")
    try:
        live = await TimewebClient(token).get_database(cluster_id)
        return timeweb_db.connection(rec, live)
    except timeweb_db.NotReady as e:
        raise HTTPException(409, str(e)) from e
    except TimewebError as e:
        raise HTTPException(502, str(e)) from e


@router.delete("/settings/timeweb")
async def delete_timeweb_token(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    user.timeweb_token_enc = None
    await db.commit()
    return {"ok": True}


@router.get("/billing")
async def billing_info(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await billing.price_book.refresh()
    rows = (
        await db.execute(
            select(LedgerEntry).where(LedgerEntry.user_id == user.id).order_by(LedgerEntry.id.desc()).limit(200)
        )
    ).scalars().all()
    s = get_settings()
    return {
        "balance_rub": billing.micro_to_rub(await billing.get_balance(db, user.id)),
        "prices": billing.price_book.public_prices(s.models),
        "payments_enabled": s.robokassa_enabled,
        "packages": s.packages,
        "topup_min_rub": s.topup_min_rub,
        "topup_max_rub": s.topup_max_rub,
        "ledger": [
            {
                "id": r.id,
                "amount_rub": billing.micro_to_rub(r.amount_micro),
                "kind": r.kind,
                "session_id": r.session_id,
                "meta": r.meta,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


class TopUp(BaseModel):
    login: str
    amount_rub: float
    comment: str | None = None


@router.get("/admin/users")
async def admin_users(_: User = Depends(admin_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(User).order_by(User.id.desc()).limit(500))).scalars().all()
    return {
        "users": [
            {"id": u.id, "login": u.login, "balance_rub": billing.micro_to_rub(u.balance_micro), "created_at": u.created_at.isoformat()}
            for u in rows
        ]
    }


@router.post("/admin/topup")
async def admin_topup(body: TopUp, admin: User = Depends(admin_user), db: AsyncSession = Depends(get_db)):
    target = (await db.execute(select(User).where(User.login.ilike(body.login)))).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "Пользователь не найден (он должен хотя бы раз войти)")
    balance = await billing.add_entry(
        db,
        target.id,
        billing.rub_to_micro(body.amount_rub),
        "topup" if body.amount_rub > 0 else "adjust",
        meta={"by": admin.login, "comment": body.comment},
    )
    return {"login": target.login, "balance_rub": billing.micro_to_rub(balance)}
