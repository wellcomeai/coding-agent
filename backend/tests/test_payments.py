import hashlib
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app import billing, config
from app.db import session_factory
from app.models import LedgerEntry, Payment, User
from app.security import SESSION_COOKIE, sign_session


@pytest.fixture
def robokassa_env(monkeypatch):
    monkeypatch.setenv("ROBOKASSA_LOGIN", "shop")
    monkeypatch.setenv("ROBOKASSA_PASSWORD1", "p1")
    monkeypatch.setenv("ROBOKASSA_PASSWORD2", "p2")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


async def test_markup_formula():
    s = config.Settings(price_markup=0, target_margin=0.25, payment_fee=0.05, tax_rate=0.06, infra_overhead=0.05)
    assert round(s.markup, 4) == round(1.25 * 1.05 / 0.89, 4)
    # чистыми после комиссии и налога остаётся себестоимость × 1.25 × 1.05
    revenue = 100 * s.markup
    assert round(revenue * (1 - 0.05 - 0.06) / 100, 4) == round(1.25 * 1.05, 4)
    assert config.Settings(price_markup=2.0).markup == 2.0


async def test_topup_flow(app_env, robokassa_env):
    from app.main import create_app

    async with session_factory()() as db:
        u = User(github_id=10, login="payer", balance_micro=0)
        db.add(u)
        await db.commit()
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        c.cookies.set(SESSION_COOKIE, sign_session(u.id))
        assert (await c.post("/api/billing/topup", json={"amount_rub": 5})).status_code == 400
        r = await c.post("/api/billing/topup", json={"amount_rub": 500})
        assert r.status_code == 200, r.text
        inv = r.json()["payment_id"]
        q = parse_qs(urlparse(r.json()["url"]).query)
        assert q["OutSum"] == ["500.00"] and q["InvId"] == [str(inv)]
        assert q["SignatureValue"] == [md5(f"shop:500.00:{inv}:p1:Shp_user={u.id}")]

        c.cookies.clear()
        # подделанная подпись отклоняется
        bad = await c.post("/api/billing/robokassa/result",
                           content=f"OutSum=500.000000&InvId={inv}&SignatureValue=deadbeef&Shp_user={u.id}",
                           headers={"content-type": "application/x-www-form-urlencoded"})
        assert bad.status_code == 400
        # неверная сумма отклоняется даже с корректной подписью
        sig_low = md5(f"1.000000:{inv}:p2:Shp_user={u.id}")
        assert (await c.post("/api/billing/robokassa/result", content=f"OutSum=1.000000&InvId={inv}&SignatureValue={sig_low}&Shp_user={u.id}",
                             headers={"content-type": "application/x-www-form-urlencoded"})).status_code == 400

        sig = md5(f"500.000000:{inv}:p2:Shp_user={u.id}").upper()
        body = f"OutSum=500.000000&InvId={inv}&SignatureValue={sig}&Shp_user={u.id}"
        for _ in range(2):  # повторное уведомление не зачисляет второй раз
            ok = await c.post("/api/billing/robokassa/result", content=body,
                              headers={"content-type": "application/x-www-form-urlencoded"})
            assert ok.status_code == 200 and ok.text == f"OK{inv}"

        succ = await c.get(f"/api/billing/robokassa/success?InvId={inv}")
        assert succ.status_code == 303 and succ.headers["location"] == f"/#/billing?paid={inv}"

    async with session_factory()() as db:
        user = await db.get(User, u.id)
        assert user.balance_micro == billing.rub_to_micro(500)
        assert (await db.get(Payment, inv)).status == "paid"
        topups = (await db.execute(select(LedgerEntry).where(LedgerEntry.kind == "topup"))).scalars().all()
        assert len(topups) == 1
