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
    """Базы, созданные агентом, с актуальным статусом из Timeweb."""
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
