"""Внутренний баланс пользователей: пополнения и списания за токены LLM.

Себестоимость берётся из прайса Timeweb Cloud AI (руб. за токен), умножается на наценку.
Все суммы хранятся в микрорублях (int), чтобы не терять точность.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .models import LedgerEntry, User
from .timeweb import TimewebClient

log = logging.getLogger(__name__)

MICRO = 1_000_000


def rub_to_micro(rub: float) -> int:
    return int(round(rub * MICRO))


def micro_to_rub(micro: int) -> float:
    return round(micro / MICRO, 4)


@dataclass
class ModelPrice:
    # руб. за 1 токен (себестоимость)
    input: float
    output: float
    cache_read: float | None = None


def _norm(name: str) -> str:
    name = name.lower().split("/")[-1]
    return re.sub(r"[^a-z0-9]", "", name)


class PriceBook:
    TTL = 3600

    def __init__(self) -> None:
        self._prices: dict[str, ModelPrice] = {}
        self._loaded_at = 0.0
        self._lock = asyncio.Lock()

    def set_prices(self, prices: dict[str, ModelPrice]) -> None:
        self._prices = {_norm(k): v for k, v in prices.items()}
        self._loaded_at = time.time()

    async def refresh(self) -> None:
        s = get_settings()
        if not s.timeweb_api_token:
            return
        async with self._lock:
            if time.time() - self._loaded_at < self.TTL and self._prices:
                return
            try:
                models = await TimewebClient(s.timeweb_api_token).ai_models()
            except Exception as e:  # noqa: BLE001
                log.warning("Не удалось загрузить прайс Timeweb: %s", e)
                self._loaded_at = time.time() - self.TTL + 300  # повторим через 5 минут
                return
            prices = {}
            for m in models:
                pv = m.get("parameter_values") or {}
                if m.get("type") != "llm" or m.get("is_stopped") or pv.get("cost_in") is None:
                    continue
                prices[m["model_name"]] = ModelPrice(pv["cost_in"], pv.get("cost_out", pv["cost_in"]), pv.get("cost_cache_read"))
            self.set_prices(prices)
            log.info("Загружен прайс Timeweb: %d моделей", len(prices))

    def get(self, model: str) -> ModelPrice:
        p = self._prices.get(_norm(model))
        if p:
            return p
        s = get_settings()
        return ModelPrice(s.default_price_in_per_m / 1e6, s.default_price_out_per_m / 1e6)

    def cost_micro(self, model: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> int:
        """Стоимость вызова для пользователя (с наценкой), в микрорублях."""
        p = self.get(model)
        cached_tokens = min(cached_tokens, prompt_tokens)
        cache_price = p.cache_read if p.cache_read is not None else p.input
        rub = (prompt_tokens - cached_tokens) * p.input + cached_tokens * cache_price + completion_tokens * p.output
        return max(1, rub_to_micro(rub * get_settings().price_markup)) if (prompt_tokens or completion_tokens) else 0

    def public_prices(self, models: list[str]) -> list[dict]:
        markup = get_settings().price_markup
        out = []
        for m in models:
            p = self.get(m)
            out.append(
                {
                    "model": m,
                    "input_per_m_rub": round(p.input * 1e6 * markup, 2),
                    "output_per_m_rub": round(p.output * 1e6 * markup, 2),
                }
            )
        return out


price_book = PriceBook()


async def add_entry(
    db: AsyncSession, user_id: int, amount_micro: int, kind: str, session_id: str | None = None, meta: dict | None = None
) -> int:
    """Атомарно изменить баланс и записать операцию в журнал. Возвращает новый баланс."""
    await db.execute(update(User).where(User.id == user_id).values(balance_micro=User.balance_micro + amount_micro))
    db.add(LedgerEntry(user_id=user_id, amount_micro=amount_micro, kind=kind, session_id=session_id, meta=meta or {}))
    await db.commit()
    return await get_balance(db, user_id)


async def get_balance(db: AsyncSession, user_id: int) -> int:
    return (await db.execute(select(User.balance_micro).where(User.id == user_id))).scalar_one()
