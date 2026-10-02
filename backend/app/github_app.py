"""Работа с GitHub App: OAuth-вход пользователя, installation-токены, REST API."""

import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
import jwt

from .config import get_settings


class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status
        self.message = message


def _headers(token: str | None = None, bearer_app: bool = False) -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}" if bearer_app else f"token {token}"
    return h


async def _request(method: str, path: str, token: str | None = None, *, app_jwt: bool = False, **kw) -> dict | list:
    s = get_settings()
    url = path if path.startswith("http") else s.github_api_url + path
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.request(method, url, headers=_headers(token, app_jwt), **kw)
    if r.status_code >= 400:
        try:
            msg = r.json().get("message", r.text)
            errors = r.json().get("errors")
            if errors:
                msg = f"{msg}: {errors}"
        except ValueError:
            msg = r.text
        raise GitHubError(r.status_code, msg)
    if r.status_code == 204 or not r.content:
        return {}
    return r.json()


# ---------- OAuth (user-to-server) ----------


def oauth_authorize_url(state: str) -> str:
    s = get_settings()
    q = urlencode(
        {
            "client_id": s.github_client_id,
            "redirect_uri": f"{s.public_url}/api/auth/github/callback",
            "state": state,
        }
    )
    return f"{s.github_web_url}/login/oauth/authorize?{q}"


def install_url() -> str:
    s = get_settings()
    return f"{s.github_web_url}/apps/{s.github_app_slug}/installations/new"


async def _token_request(data: dict) -> dict:
    s = get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{s.github_web_url}/login/oauth/access_token",
            data={"client_id": s.github_client_id, "client_secret": s.github_client_secret, **data},
            headers={"Accept": "application/json"},
        )
    body = r.json()
    if "access_token" not in body:
        raise GitHubError(r.status_code, body.get("error_description") or body.get("error") or "oauth error")
    return body


async def exchange_code(code: str) -> dict:
    return await _token_request({"code": code})


async def refresh_token(refresh: str) -> dict:
    return await _token_request({"grant_type": "refresh_token", "refresh_token": refresh})


def token_expiry(body: dict) -> datetime | None:
    if body.get("expires_in"):
        return datetime.now(UTC) + timedelta(seconds=int(body["expires_in"]))
    return None


async def get_user(token: str) -> dict:
    return await _request("GET", "/user", token)  # type: ignore[return-value]


async def list_user_repos(token: str) -> list[dict]:
    """Все репозитории, к которым у пользователя есть доступ через установки нашего App."""
    out: list[dict] = []
    installs = await _request("GET", "/user/installations?per_page=100", token)
    for inst in installs.get("installations", []):  # type: ignore[union-attr]
        page = 1
        while True:
            data = await _request(
                "GET", f"/user/installations/{inst['id']}/repositories?per_page=100&page={page}", token
            )
            repos = data.get("repositories", [])  # type: ignore[union-attr]
            for r in repos:
                out.append(
                    {
                        "full_name": r["full_name"],
                        "private": r["private"],
                        "default_branch": r.get("default_branch") or "main",
                        "installation_id": inst["id"],
                        "permissions": r.get("permissions", {}),
                        "pushed_at": r.get("pushed_at"),
                    }
                )
            if len(repos) < 100:
                break
            page += 1
    out.sort(key=lambda r: r.get("pushed_at") or "", reverse=True)
    return out


# ---------- Installation tokens (server-to-server) ----------


def app_jwt() -> str:
    s = get_settings()
    now = int(time.time())
    return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": s.github_app_id}, s.github_private_key_pem(), "RS256")


_inst_cache: dict[tuple[int, str], tuple[str, datetime]] = {}


async def installation_token(installation_id: int, repo_full_name: str) -> str:
    """Короткоживущий токен, ограниченный одним репозиторием."""
    key = (installation_id, repo_full_name)
    cached = _inst_cache.get(key)
    if cached and cached[1] - datetime.now(UTC) > timedelta(minutes=5):
        return cached[0]
    body = await _request(
        "POST",
        f"/app/installations/{installation_id}/access_tokens",
        app_jwt(),
        app_jwt=True,
        json={"repositories": [repo_full_name.split("/", 1)[1]]},
    )
    token = body["token"]  # type: ignore[index]
    exp = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))  # type: ignore[index]
    _inst_cache[key] = (token, exp)
    return token


# ---------- Репозиторий ----------


async def list_branches(token: str, repo: str) -> list[str]:
    data = await _request("GET", f"/repos/{repo}/branches?per_page=100", token)
    return [b["name"] for b in data]  # type: ignore[union-attr]


async def branch_sha(token: str, repo: str, branch: str) -> str:
    data = await _request("GET", f"/repos/{repo}/branches/{branch}", token)
    return data["commit"]["sha"]  # type: ignore[index]


async def find_pull_request(token: str, repo: str, head_branch: str) -> dict | None:
    owner = repo.split("/")[0]
    data = await _request("GET", f"/repos/{repo}/pulls?state=open&head={owner}:{head_branch}", token)
    return data[0] if data else None  # type: ignore[index]


async def create_pull_request(
    token: str, repo: str, head: str, base: str, title: str, body: str, draft: bool = False
) -> dict:
    return await _request(  # type: ignore[return-value]
        "POST",
        f"/repos/{repo}/pulls",
        token,
        json={"title": title, "head": head, "base": base, "body": body, "draft": draft},
    )


async def get_pull_request_status(token: str, repo: str, number: int) -> dict:
    pr = await _request("GET", f"/repos/{repo}/pulls/{number}", token)
    checks = await _request("GET", f"/repos/{repo}/commits/{pr['head']['sha']}/check-runs", token)  # type: ignore[index]
    return {
        "state": pr["state"],  # type: ignore[index]
        "merged": pr.get("merged"),  # type: ignore[union-attr]
        "mergeable": pr.get("mergeable"),  # type: ignore[union-attr]
        "checks": [
            {"name": c["name"], "status": c["status"], "conclusion": c.get("conclusion")}
            for c in checks.get("check_runs", [])  # type: ignore[union-attr]
        ],
    }


async def comment_issue(token: str, repo: str, number: int, body: str) -> dict:
    return await _request("POST", f"/repos/{repo}/issues/{number}/comments", token, json={"body": body})  # type: ignore[return-value]
