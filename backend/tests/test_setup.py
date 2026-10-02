import httpx
import pytest

from app import config
from app.models import AppConfig
from app.db import session_factory
from app.security import decrypt


@pytest.fixture
def setup_token(monkeypatch):
    for k in ("GITHUB_APP_ID", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "GITHUB_PRIVATE_KEY"):
        monkeypatch.setenv(k, "")
    monkeypatch.setenv("SETUP_TOKEN", "tok123")
    monkeypatch.setenv("PUBLIC_URL", "https://agent.example")
    config.get_settings.cache_clear()
    yield "tok123"
    config.get_settings.cache_clear()


async def test_setup_page_requires_token_and_renders_manifest(app_env, setup_token):
    from app.main import create_app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
        assert (await c.get("/setup")).status_code == 403
        assert (await c.get("/setup?token=wrong")).status_code == 403
        r = await c.get("/setup?token=tok123")
        assert r.status_code == 200
        assert "https://agent.example/api/auth/github/callback" in r.text
        assert '"contents": "write"' in r.text
        assert (await c.get("/api/status")).json()["github_configured"] is False


async def test_setup_callback_stores_and_applies_credentials(app_env, setup_token, monkeypatch):
    from app.main import create_app
    from app.security import sign_state

    async def fake_convert(code):
        assert code == "CODE"
        return {"id": 99, "slug": "coding-agent-x", "client_id": "Iv1.abc", "client_secret": "sec",
                "pem": "-----BEGIN RSA PRIVATE KEY-----\nX\n-----END RSA PRIVATE KEY-----\n"}

    monkeypatch.setattr("app.routers.setup.convert_manifest", fake_convert)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
        bad = await c.get("/api/setup/github/callback?code=CODE&state=forged")
        assert bad.status_code == 400
        r = await c.get(f"/api/setup/github/callback?code=CODE&state={sign_state({'setup': True})}")
        assert r.status_code == 303
        assert r.headers["location"].endswith("/apps/coding-agent-x/installations/new")
        assert (await c.get("/api/status")).json()["github_configured"] is True

    s = config.get_settings()
    assert (s.github_app_id, s.github_client_id) == ("99", "Iv1.abc")
    async with session_factory()() as db:
        row = await db.get(AppConfig, "github_client_secret")
        assert row.value_enc != "sec" and decrypt(row.value_enc) == "sec"

    # после «перезапуска» значения подтягиваются из БД
    config.get_settings.cache_clear()
    from app.routers.setup import apply_db_config
    await apply_db_config()
    assert config.get_settings().github_app_slug == "coding-agent-x"
