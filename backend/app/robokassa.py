"""Робокасса: формирование ссылки на оплату и проверка подписей уведомлений.

Документация: https://docs.robokassa.ru/pay-interface/
"""

import hashlib
import json
from urllib.parse import quote, urlencode

from .config import get_settings

PAY_URL = "https://auth.robokassa.ru/Merchant/Index.aspx"


def _hash(value: str) -> str:
    algo = get_settings().robokassa_hash.lower()
    return hashlib.new(algo, value.encode()).hexdigest()


def _passwords(is_test: bool) -> tuple[str, str]:
    s = get_settings()
    if is_test:
        return s.robokassa_test_password1, s.robokassa_test_password2
    return s.robokassa_password1, s.robokassa_password2


def _shp(params: dict[str, str]) -> str:
    """Пользовательские параметры Shp_* добавляются к подписи в алфавитном порядке."""
    return "".join(f":{k}={params[k]}" for k in sorted(params))


def format_sum(amount_kop: int) -> str:
    return f"{amount_kop // 100}.{amount_kop % 100:02d}"


def receipt(amount_kop: int, description: str) -> str:
    s = get_settings()
    return json.dumps(
        {
            "sno": s.robokassa_receipt_sno,
            "items": [
                {
                    "name": description[:128],
                    "quantity": 1,
                    "sum": float(format_sum(amount_kop)),
                    "payment_method": "full_prepayment",
                    "payment_object": "service",
                    "tax": s.robokassa_receipt_tax,
                }
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def payment_url(inv_id: int, amount_kop: int, description: str, email: str | None, shp: dict[str, str], is_test: bool) -> str:
    s = get_settings()
    out_sum = format_sum(amount_kop)
    password1, _ = _passwords(is_test)
    rec = receipt(amount_kop, description) if s.robokassa_receipt else None
    rec_q = quote(rec, safe="") if rec else None
    base = f"{s.robokassa_login}:{out_sum}:{inv_id}"
    if rec_q:
        base += f":{rec_q}"
    signature = _hash(f"{base}:{password1}{_shp(shp)}")
    params = {
        "MerchantLogin": s.robokassa_login,
        "OutSum": out_sum,
        "InvId": str(inv_id),
        "Description": description,
        "SignatureValue": signature,
        "Culture": "ru",
        "Encoding": "utf-8",
        **shp,
    }
    if email:
        params["Email"] = email
    if is_test:
        params["IsTest"] = "1"
    query = urlencode(params)
    if rec_q:
        query += f"&Receipt={quote(rec_q, safe='')}"
    return f"{PAY_URL}?{query}"


def _check(out_sum: str, inv_id: str, signature: str, password: str, shp: dict[str, str]) -> bool:
    expected = _hash(f"{out_sum}:{inv_id}:{password}{_shp(shp)}")
    return bool(signature) and expected.lower() == signature.lower()


def check_result(out_sum: str, inv_id: str, signature: str, shp: dict[str, str], is_test: bool) -> bool:
    """Подпись уведомления на Result URL (пароль №2)."""
    return _check(out_sum, inv_id, signature, _passwords(is_test)[1], shp)


def check_success(out_sum: str, inv_id: str, signature: str, shp: dict[str, str], is_test: bool) -> bool:
    """Подпись редиректа на Success URL (пароль №1)."""
    return _check(out_sum, inv_id, signature, _passwords(is_test)[0], shp)
