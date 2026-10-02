"""Мастер первичной настройки: автосоздание GitHub App через GitHub App Manifest.

Администратор открывает /setup?token=<SETUP_TOKEN>, нажимает кнопку — GitHub создаёт приложение
с нужными правами и адресами и возвращает его ключи на /api/setup/github/callback.
Ключи сохраняются в БД в зашифрованном виде и применяются без перезапуска.
"""

import html
import json
import secrets

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from .. import github_app
from ..config import get_settings
from ..db import session_factory
from ..models import AppConfig
from ..security import decrypt, encrypt, read_state, sign_state

router = APIRouter(tags=["setup"])

GITHUB_KEYS = {
    "github_app_id": "id",
    "github_app_slug": "slug",
    "github_client_id": "client_id",
    "github_client_secret": "client_secret",
    "github_private_key": "pem",
}


async def apply_db_config() -> None:
    """Подставить сохранённые в БД настройки, если они не заданы через ENV."""
    s = get_settings()
    if s.github_configured:
        return
    async with session_factory()() as db:
        rows = (await db.execute(select(AppConfig))).scalars().all()
    values = {r.key: decrypt(r.value_enc) for r in rows}
    if all(values.get(k) for k in GITHUB_KEYS):
        for k in GITHUB_KEYS:
            setattr(s, k, values[k])


def _check_token(token: str | None) -> None:
    s = get_settings()
    if not s.setup_token or not token or not secrets.compare_digest(token, s.setup_token):
        raise HTTPException(403, "Неверный или отсутствующий токен настройки")


def manifest(name: str) -> dict:
    url = get_settings().public_url
    return {
        "name": name,
        "url": url,
        "description": "AI-агент для работы с репозиториями и деплоя в Timeweb Cloud",
        "hook_attributes": {"url": f"{url}/api/github/webhook", "active": False},
        "redirect_url": f"{url}/api/setup/github/callback",
        "callback_urls": [f"{url}/api/auth/github/callback"],
        "request_oauth_on_install": True,
        "public": True,
        "default_permissions": {
            "contents": "write",
            "pull_requests": "write",
            "checks": "read",
            "statuses": "read",
            "metadata": "read",
        },
        "default_events": [],
    }


PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Настройка Coding Agent</title>
<style>
 body{{font-family:system-ui,sans-serif;max-width:620px;margin:40px auto;padding:0 16px;line-height:1.5;color:#1c2024;background:#f6f7f9}}
 .card{{background:#fff;border:1px solid #e3e6ea;border-radius:12px;padding:20px 24px}}
 label{{display:block;margin:14px 0 4px;font-size:14px;color:#555}}
 input{{width:100%;box-sizing:border-box;padding:9px 10px;border:1px solid #ccc;border-radius:8px;font:inherit}}
 button{{margin-top:18px;background:#2563eb;color:#fff;border:0;border-radius:8px;padding:11px 20px;font:inherit;cursor:pointer}}
 .muted{{color:#6b7280;font-size:14px}} .ok{{color:#16a34a}}
</style></head><body><div class="card">{body}</div></body></html>"""


@router.get("/setup", response_class=HTMLResponse, include_in_schema=False)
async def setup_page(token: str | None = None):
    _check_token(token)
    s = get_settings()
    if s.github_configured:
        body = (
            "<h2>✅ GitHub App уже настроен</h2>"
            f"<p>Приложение: <a href='https://github.com/apps/{html.escape(s.github_app_slug)}'>{html.escape(s.github_app_slug)}</a></p>"
            f"<p><a href='{html.escape(github_app.install_url())}'>Установить на репозитории</a> · <a href='/'>Открыть сервис</a></p>"
        )
        return PAGE.format(body=body)
    state = sign_state({"setup": True})
    default_name = f"Coding Agent {secrets.token_hex(2)}"
    body = f"""
<h2>Создание GitHub App</h2>
<p class="muted">GitHub создаст приложение с нужными правами (код и pull request'ы — чтение/запись,
CI — чтение) и адресами этого сервера: <b>{html.escape(s.public_url)}</b>. Ключи вернутся сюда автоматически.</p>
<form id="f" method="post">
  <label>Название приложения (должно быть уникальным на GitHub)</label>
  <input id="name" value="{html.escape(default_name)}" required>
  <label>Организация GitHub (оставьте пустым, чтобы создать в личном аккаунте)</label>
  <input id="org" placeholder="my-org">
  <input type="hidden" name="manifest" id="manifest">
  <button type="submit">Создать GitHub App</button>
</form>
<script>
const base = {json.dumps(manifest("__NAME__"))};
document.getElementById('f').addEventListener('submit', (e) => {{
  const m = Object.assign({{}}, base, {{ name: document.getElementById('name').value.trim() }});
  document.getElementById('manifest').value = JSON.stringify(m);
  const org = document.getElementById('org').value.trim();
  const st = encodeURIComponent({json.dumps(state)});
  e.target.action = org
    ? 'https://github.com/organizations/' + encodeURIComponent(org) + '/settings/apps/new?state=' + st
    : 'https://github.com/settings/apps/new?state=' + st;
}});
</script>"""
    return PAGE.format(body=body)


async def convert_manifest(code: str) -> dict:
    """Обменять одноразовый code на ключи созданного GitHub App."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{get_settings().github_api_url}/app-manifests/{code}/conversions",
            headers={"Accept": "application/vnd.github+json"},
        )
    if r.status_code >= 400:
        raise HTTPException(502, f"GitHub не выдал ключи приложения: {r.text[:300]}")
    return r.json()


@router.get("/api/setup/github/callback", include_in_schema=False)
async def setup_callback(code: str, state: str):
    st = read_state(state, max_age=3600)
    if not st or not st.get("setup"):
        raise HTTPException(400, "Некорректный state, начните настройку заново")
    s = get_settings()
    data = await convert_manifest(code)
    values = {k: str(data[src]) for k, src in GITHUB_KEYS.items()}
    async with session_factory()() as db:
        for k, v in values.items():
            row = await db.get(AppConfig, k)
            if row:
                row.value_enc = encrypt(v)
            else:
                db.add(AppConfig(key=k, value_enc=encrypt(v)))
        await db.commit()
    for k, v in values.items():
        setattr(s, k, v)
    # Сразу ведём на установку приложения; после неё GitHub вернёт пользователя на вход
    return RedirectResponse(github_app.install_url(), status_code=303)


@router.get("/api/status")
async def status():
    s = get_settings()
    return {"github_configured": s.github_configured, "ai_configured": bool(s.ai_gateway_api_key)}
