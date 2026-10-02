from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from . import github_app
from .config import get_settings
from .db import get_db
from .models import User
from .security import SESSION_COOKIE, decrypt, encrypt, read_session


async def current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    uid = read_session(request.cookies.get(SESSION_COOKIE))
    user = await db.get(User, uid) if uid else None
    if not user:
        raise HTTPException(401, "Требуется вход через GitHub")
    return user


async def admin_user(user: User = Depends(current_user)) -> User:
    if user.login.lower() not in get_settings().admin_logins:
        raise HTTPException(403, "Только для администраторов")
    return user


async def user_github_token(db: AsyncSession, user: User) -> str:
    """User-to-server токен; обновляется по refresh-токену, если истёк."""
    token = decrypt(user.gh_token_enc)
    exp = user.gh_token_expires_at
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    if token and (exp is None or exp - datetime.now(UTC) > timedelta(minutes=2)):
        return token
    refresh = decrypt(user.gh_refresh_enc)
    if not refresh:
        raise HTTPException(401, "Сессия GitHub истекла, войдите заново")
    try:
        body = await github_app.refresh_token(refresh)
    except github_app.GitHubError as e:
        raise HTTPException(401, "Сессия GitHub истекла, войдите заново") from e
    user.gh_token_enc = encrypt(body["access_token"])
    if body.get("refresh_token"):
        user.gh_refresh_enc = encrypt(body["refresh_token"])
    user.gh_token_expires_at = github_app.token_expiry(body)
    await db.commit()
    return body["access_token"]
