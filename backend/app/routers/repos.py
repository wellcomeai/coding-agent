from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .. import github_app
from ..db import get_db
from ..deps import current_user, user_github_token
from ..models import User

router = APIRouter(prefix="/api/repos", tags=["repos"])


@router.get("")
async def list_repos(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    token = await user_github_token(db, user)
    try:
        repos = await github_app.list_user_repos(token)
    except github_app.GitHubError as e:
        raise HTTPException(502, str(e)) from e
    return {"repos": repos, "install_url": github_app.install_url()}


@router.get("/{owner}/{repo}/branches")
async def list_branches(owner: str, repo: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    token = await user_github_token(db, user)
    try:
        return {"branches": await github_app.list_branches(token, f"{owner}/{repo}")}
    except github_app.GitHubError as e:
        raise HTTPException(e.status if e.status < 500 else 502, e.message) from e
