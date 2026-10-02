from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import billing
from ..config import get_settings
from ..db import get_db
from ..deps import admin_user, current_user
from ..models import LedgerEntry, User
from ..security import encrypt
from ..timeweb import TimewebClient, TimewebError

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
    return {
        "balance_rub": billing.micro_to_rub(await billing.get_balance(db, user.id)),
        "prices": billing.price_book.public_prices(get_settings().models),
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
