import json

from app.agent import tools as T
from app.timeweb import HIDDEN_ENV, TimewebClient
from app.timeweb_db import get_record


class DbFake(TimewebClient):
    def __init__(self, app_envs=None, db_status="started", ip="5.129.242.60"):
        super().__init__("token")
        self.app_envs = dict(app_envs or {})
        self.db_status, self.ip = db_status, ip
        self.created = None
        self.patches = []
        self.deploys = []

    async def request(self, method, path, **kw):
        if method == "POST" and path == "/api/v1/databases":
            self.created = kw["json"]
            return {"db": {"id": 777, "status": "creating"}}
        if path == "/api/v1/databases/777":
            nets = [{"type": "public", "ips": [{"type": "ipv_4", "ip": self.ip}]}] if self.ip else []
            return {"db": {"id": 777, "name": "shop-db", "type": "postgres16", "status": self.db_status, "port": 5432, "networks": nets}}
        if path == "/api/v1/apps/5" and method == "GET":
            return {"app": {"id": 5, "branch_name": None, "envs": self.app_envs}}
        if path == "/api/v1/apps/5" and method == "PATCH":
            self.patches.append(kw["json"])
            self.app_envs = {k: HIDDEN_ENV for k in kw["json"]["envs"]}
            return {}
        if path.startswith("/api/v1/apps/5/deploys"):
            return {"deploys": [{"id": "d1", "commit_sha": "abc123"}]}
        if path == "/api/v1/apps/5/deploy":
            self.deploys.append(kw["json"])
            return {"deploy": {"id": "d2", "status": "deploy", "commit_sha": kw["json"]["commit_sha"]}}
        raise AssertionError(f"unexpected {method} {path}")


class SB:
    repo_dir = "/w/repo"


def ctx(tw, user_id):
    return T.ToolContext(sandbox=SB(), repo="o/r", installation_id=1, base_branch="main", work_branch="agent/x",
                         timeweb=tw, user_id=user_id)


async def _user():
    from app.db import session_factory
    from app.models import User

    async with session_factory()() as db:
        u = User(github_id=9, login="dbuser", balance_micro=0)
        db.add(u)
        await db.commit()
        return u.id


async def run(c, tool, **args):
    _, reg = T.toolset(True)
    return await T.execute(c, reg, tool, json.dumps(args))


async def test_create_requires_confirmation_and_hides_password(app_env):
    uid = await _user()
    tw = DbFake()
    out, err = await run(ctx(tw, uid), "timeweb_create_database", name="shop-db", db_type="postgres16", preset_id=357, confirmed=False)
    assert err and tw.created is None

    c = ctx(tw, uid)
    out, err = await run(c, "timeweb_create_database", name="shop-db", db_type="postgres16", preset_id=357, confirmed=True)
    assert not err and "777" in out
    admin = tw.created["admin"]
    assert tw.created["type"] == "postgres16" and tw.created["instance"]["name"] == "app"
    assert "CREATE" in admin["privileges"] and admin["password"] not in out
    rec = await get_record(uid, 777)
    assert rec and rec.login == admin["login"] and admin["password"] not in rec.password_enc


async def test_connect_injects_url_without_leaking_password(app_env):
    uid = await _user()
    tw = DbFake(app_envs={"SECRET_KEY": "visible-value"})
    c = ctx(tw, uid)
    await run(c, "timeweb_create_database", name="shop-db", db_type="postgres16", preset_id=357, confirmed=True)
    password = tw.created["admin"]["password"]

    out, err = await run(c, "timeweb_connect_database", app_id="5", database_id=777, url_scheme="postgresql+asyncpg")
    assert not err, out
    envs = tw.patches[0]["envs"]
    assert envs["SECRET_KEY"] == "visible-value"  # остальные переменные сохранены
    assert envs["DATABASE_URL"] == f"postgresql+asyncpg://{tw.created['admin']['login']}:{password}@5.129.242.60:5432/app"
    assert password not in out and "DATABASE_URL" in out
    assert tw.deploys == [{"commit_sha": "abc123"}]  # ветка не указана → пересборка последнего коммита


async def test_connect_refuses_when_envs_hidden(app_env):
    uid = await _user()
    tw = DbFake(app_envs={"SECRET_KEY": HIDDEN_ENV})
    c = ctx(tw, uid)
    await run(c, "timeweb_create_database", name="shop-db", db_type="postgres16", preset_id=357, confirmed=True)
    out, err = await run(c, "timeweb_connect_database", app_id="5", database_id=777)
    assert err and "скрывает" in out and tw.patches == []


async def test_connect_waits_for_ip_and_unknown_db(app_env):
    uid = await _user()
    tw = DbFake(db_status="creating", ip=None)
    c = ctx(tw, uid)
    await run(c, "timeweb_create_database", name="shop-db", db_type="postgres16", preset_id=357, confirmed=True)
    out, err = await run(c, "timeweb_connect_database", app_id="5", database_id=777)
    assert err and "публичного IP" in out
    out, err = await run(ctx(tw, uid), "timeweb_connect_database", app_id="5", database_id=12345)
    assert err and "не через агента" in out


async def test_settings_database_endpoints(app_env, monkeypatch):
    import httpx

    from app.db import session_factory
    from app.main import create_app
    from app.models import User
    from app.security import SESSION_COOKIE, encrypt, sign_session

    uid = await _user()
    tw = DbFake()
    await run(ctx(tw, uid), "timeweb_create_database", name="shop-db", db_type="postgres16", preset_id=357, confirmed=True)
    async with session_factory()() as db:
        (await db.get(User, uid)).timeweb_token_enc = encrypt("t" * 30)
        other = User(github_id=99, login="other", balance_micro=0, timeweb_token_enc=encrypt("t" * 30))
        db.add(other)
        await db.commit()
    monkeypatch.setattr("app.routers.account.TimewebClient", lambda token: tw)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
        c.cookies.set(SESSION_COOKIE, sign_session(uid))
        dbs = (await c.get("/api/timeweb/databases")).json()["databases"]
        assert dbs[0]["id"] == 777 and dbs[0]["status"] == "started" and "password" not in dbs[0]
        conn = (await c.post("/api/timeweb/databases/777/connection")).json()
        assert conn["url"].startswith("postgresql://") and conn["password"] == tw.created["admin"]["password"]
        c.cookies.set(SESSION_COOKIE, sign_session(other.id))
        assert (await c.post("/api/timeweb/databases/777/connection")).status_code == 404


async def test_price_book_falls_back_to_admin_token(app_env, monkeypatch):
    from app import billing, config
    from app.db import session_factory
    from app.models import User
    from app.security import encrypt
    from app.timeweb import TimewebError

    monkeypatch.setenv("TIMEWEB_API_TOKEN", "revoked")
    monkeypatch.setenv("ADMIN_GITHUB_LOGINS", "boss")
    config.get_settings.cache_clear()
    async with session_factory()() as db:
        db.add(User(github_id=50, login="Boss", balance_micro=0, timeweb_token_enc=encrypt("good-token")))
        await db.commit()

    async def ai_models(self):
        if self.token != "good-token":
            raise TimewebError(403, "Forbidden")
        return [{"type": "llm", "model_name": "anthropic/claude-sonnet-5",
                 "parameter_values": {"cost_in": 0.0004, "cost_out": 0.002}}]

    monkeypatch.setattr(TimewebClient, "ai_models", ai_models)
    pb = billing.PriceBook()
    await pb.refresh()
    assert pb.get("anthropic/claude-sonnet-5").input == 0.0004
    config.get_settings.cache_clear()
