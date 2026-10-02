import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import billing, github_app
from ..config import get_settings
from ..db import get_db
from ..deps import current_user
from ..models import User
from ..security import SESSION_COOKIE, SESSION_MAX_AGE, encrypt, read_state, sign_session, sign_state

router = APIRouter(prefix="/api/auth", tags=["auth"])
NONCE_COOKIE = "ca_oauth_nonce"


@router.get("/github/login")
async def github_login():
    s = get_settings()
    if not s.github_client_id:
        raise HTTPException(500, "GitHub App не настроен (GITHUB_CLIENT_ID)")
    nonce = secrets.token_urlsafe(16)
    resp = RedirectResponse(github_app.oauth_authorize_url(sign_state({"n": nonce})))
    resp.set_cookie(NONCE_COOKIE, nonce, max_age=600, httponly=True, samesite="lax", secure=s.cookie_secure)
    return resp


@router.get("/github/callback")
async def github_callback(request: Request, code: str | None = None, state: str | None = None, db: AsyncSession = Depends(get_db)):
    s = get_settings()
    st = read_state(state) if state else None
    if not code or not st or st.get("n") != request.cookies.get(NONCE_COOKIE):
        # Возврат после установки App без нашего state — запускаем вход заново
        if request.query_params.get("setup_action") or request.query_params.get("installation_id"):
            return RedirectResponse("/api/auth/github/login")
        raise HTTPException(400, "Некорректный state OAuth, попробуйте войти ещё раз")

    tokens = await github_app.exchange_code(code)
    gh = await github_app.get_user(tokens["access_token"])
    user = (await db.execute(select(User).where(User.github_id == gh["id"]))).scalar_one_or_none()
    is_new = user is None
    if is_new:
        user = User(github_id=gh["id"], login=gh["login"], balance_micro=0)
        db.add(user)
    user.login = gh["login"]
    user.name = gh.get("name")
    user.avatar_url = gh.get("avatar_url")
    user.gh_token_enc = encrypt(tokens["access_token"])
    user.gh_refresh_enc = encrypt(tokens["refresh_token"]) if tokens.get("refresh_token") else None
    user.gh_token_expires_at = github_app.token_expiry(tokens)
    await db.commit()
    if is_new and s.signup_bonus_rub > 0:
        await billing.add_entry(db, user.id, billing.rub_to_micro(s.signup_bonus_rub), "bonus", meta={"reason": "signup"})

    resp = RedirectResponse("/")
    resp.delete_cookie(NONCE_COOKIE)
    resp.set_cookie(
        SESSION_COOKIE, sign_session(user.id), max_age=SESSION_MAX_AGE, httponly=True, samesite="lax", secure=s.cookie_secure
    )
    return resp


@router.post("/logout")
async def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@router.get("/me")
async def me(user: User = Depends(current_user)):
    s = get_settings()
    return {
        "id": user.id,
        "login": user.login,
        "name": user.name,
        "avatar_url": user.avatar_url,
        "balance_rub": billing.micro_to_rub(user.balance_micro),
        "has_timeweb": bool(user.timeweb_token_enc),
        "is_admin": user.login.lower() in s.admin_logins,
        "install_url": github_app.install_url(),
        "models": s.models,
        "default_model": s.default_model,
    }
