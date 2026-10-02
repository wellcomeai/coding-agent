"""Шифрование секретов пользователей и подпись cookie сессии."""

from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import get_settings

SESSION_COOKIE = "ca_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30


def _fernet() -> Fernet:
    key = get_settings().secret_key
    if not key:
        raise RuntimeError("SECRET_KEY не задан: сгенерируйте Fernet-ключ и положите в ENV")
    return Fernet(key.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return None


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="session")


def sign_session(user_id: int) -> str:
    return _serializer().dumps({"uid": user_id})


def read_session(cookie: str | None) -> int | None:
    if not cookie:
        return None
    try:
        data = _serializer().loads(cookie, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return None
    return int(data.get("uid")) if data.get("uid") else None


def sign_state(payload: dict) -> str:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="oauth-state").dumps(payload)


def read_state(state: str, max_age: int = 600) -> dict | None:
    try:
        return URLSafeTimedSerializer(get_settings().session_secret, salt="oauth-state").loads(state, max_age=max_age)
    except BadSignature:
        return None


def mask(secret: str | None) -> str | None:
    if not secret:
        return None
    return secret[:4] + "…" + secret[-4:] if len(secret) > 12 else "…"
