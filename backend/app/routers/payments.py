import logging
from datetime import UTC, datetime
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import billing, robokassa
from ..config import get_settings
from ..db import get_db, session_factory
from ..deps import current_user
from ..models import Payment, User

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/billing", tags=["payments"])


class TopUpRequest(BaseModel):
    amount_rub: int


@router.post("/topup")
async def create_topup(body: TopUpRequest, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    s = get_settings()
    if not s.robokassa_enabled:
        raise HTTPException(503, "Онлайн-оплата пока не подключена")
    if not s.topup_min_rub <= body.amount_rub <= s.topup_max_rub:
        raise HTTPException(400, f"Сумма пополнения — от {s.topup_min_rub} до {s.topup_max_rub} ₽")
    payment = Payment(user_id=user.id, amount_kop=body.amount_rub * 100, is_test=s.robokassa_test)
    db.add(payment)
    await db.commit()
    url = robokassa.payment_url(
        payment.id,
        payment.amount_kop,
        f"Пополнение баланса Coding Agent ({user.login})",
        None,
        {"Shp_user": str(user.id)},
        payment.is_test,
    )
    return {"payment_id": payment.id, "url": url}


@router.get("/payments/{payment_id}")
async def payment_status(payment_id: int, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    p = await db.get(Payment, payment_id)
    if not p or p.user_id != user.id:
        raise HTTPException(404, "Платёж не найден")
    return {"id": p.id, "status": p.status, "amount_rub": p.amount_kop / 100}


async def _params(request: Request) -> dict[str, str]:
    params = dict(request.query_params)
    if request.method == "POST":
        params.update(dict(parse_qsl((await request.body()).decode("utf-8", errors="replace"))))
    return params


def _shp(params: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in params.items() if k.lower().startswith("shp_")}


@router.api_route("/robokassa/result", methods=["GET", "POST"], include_in_schema=False)
async def robokassa_result(request: Request):
    """Серверное уведомление об оплате. Только здесь зачисляются деньги."""
    p = await _params(request)
    out_sum, inv_id, sig = p.get("OutSum", ""), p.get("InvId", ""), p.get("SignatureValue", "")
    if not inv_id.isdigit():
        return PlainTextResponse("bad request", status_code=400)
    async with session_factory()() as db:
        payment = await db.get(Payment, int(inv_id))
        if not payment:
            return PlainTextResponse("unknown invoice", status_code=404)
        if not robokassa.check_result(out_sum, inv_id, sig, _shp(p), payment.is_test):
            log.warning("Робокасса: неверная подпись для InvId=%s", inv_id)
            return PlainTextResponse("bad sign", status_code=400)
        try:
            paid_kop = round(float(out_sum) * 100)
        except ValueError:
            return PlainTextResponse("bad sum", status_code=400)
        if paid_kop != payment.amount_kop:
            log.error("Робокасса: сумма %s не совпадает со счётом %s", out_sum, inv_id)
            return PlainTextResponse("bad sum", status_code=400)
        # Атомарно: зачисляем только один раз, даже если уведомление пришло повторно
        res = await db.execute(
            update(Payment)
            .where(Payment.id == payment.id, Payment.status == "pending")
            .values(status="paid", paid_at=datetime.now(UTC), meta={"fee": p.get("Fee"), "method": p.get("PaymentMethod")})
        )
        await db.commit()
        if res.rowcount == 1:
            await billing.add_entry(
                db,
                payment.user_id,
                billing.rub_to_micro(payment.amount_kop / 100),
                "topup",
                meta={"payment_id": payment.id, "provider": "robokassa", "test": payment.is_test},
            )
            log.info("Робокасса: оплачен счёт %s на %s ₽", inv_id, out_sum)
    return PlainTextResponse(f"OK{inv_id}")


@router.api_route("/robokassa/success", methods=["GET", "POST"], include_in_schema=False)
async def robokassa_success(request: Request):
    p = await _params(request)
    return RedirectResponse(f"/#/billing?paid={p.get('InvId', '')}", status_code=303)


@router.api_route("/robokassa/fail", methods=["GET", "POST"], include_in_schema=False)
async def robokassa_fail(request: Request):
    p = await _params(request)
    return RedirectResponse(f"/#/billing?failed={p.get('InvId', '')}", status_code=303)
