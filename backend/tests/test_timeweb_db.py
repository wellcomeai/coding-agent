import json

from app.agent import tools as T
from app.timeweb import HIDDEN_ENV, TimewebClient, TimewebError
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
        if path == "/api/v1/databases/777/admins" and method == "GET":
            return {"admins": [{"id": 31, "login": "u1", "status": "created"}]}
        if path == "/api/v1/databases/777/instances":
            return {"instances": [{"id": 41, "name": "app"}]}
        if path == "/api/v1/databases/777/admins/31" and method == "PATCH":
            self.regrants = getattr(self, "regrants", []) + [kw["json"]]
            return {}
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


async def test_connect_waits_for_ip_and_unknown_db(app_env, monkeypatch):
    monkeypatch.setattr(T, "WAIT_POLL_SECONDS", 0)
    monkeypatch.setattr(T, "_wait_deadline", lambda _m: 0)
    uid = await _user()
    tw = DbFake(db_status="creating", ip=None)
    c = ctx(tw, uid)
    await run(c, "timeweb_create_database", name="shop-db", db_type="postgres16", preset_id=357, confirmed=True)
    out, err = await run(c, "timeweb_connect_database", app_id="5", database_id=777)
    assert err and "не готова" in out
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


class WaitFake(DbFake):
    def __init__(self, deploy_states, db_states):
        super().__init__()
        self.deploy_states, self.db_states = list(deploy_states), list(db_states)

    async def request(self, method, path, **kw):
        if path.startswith("/api/v1/apps/5/deploys"):
            status = self.deploy_states.pop(0) if len(self.deploy_states) > 1 else self.deploy_states[0]
            ended = "2026-10-01T10:00:00Z" if status in ("success", "failure") else None
            return {"deploys": [{"id": "d2", "status": status, "ended_at": ended}, {"id": "d1", "status": "success"}]}
        if path == "/api/v1/apps/5/deploy/d2/logs":
            return {"deploy_logs": ["npm ERR! build failed"]}
        if path == "/api/v1/databases/777":
            self.db_status, self.ip = self.db_states.pop(0) if len(self.db_states) > 1 else self.db_states[0]
        return await super().request(method, path, **kw)


async def test_wait_deploy_polls_on_server_and_returns_logs_on_failure(app_env, monkeypatch):
    monkeypatch.setattr(T, "WAIT_POLL_SECONDS", 0)
    tw = WaitFake(["deploy", "deploy", "failure"], [])
    out, err = await run(ctx(tw, 1), "timeweb_wait_deploy", app_id="5", deploy_id="d2")
    data = json.loads(out)
    assert not err and data["finished"] and data["deploy"]["status"] == "failure"
    assert "build failed" in data["deploy_logs_tail"] and tw.deploy_states == ["failure"]

    tw = WaitFake(["success"], [])
    data = json.loads((await run(ctx(tw, 1), "timeweb_wait_deploy", app_id="5"))[0])
    assert data["finished"] and "deploy_logs_tail" not in data


async def test_wait_deploy_gives_up_after_timeout(app_env, monkeypatch):
    monkeypatch.setattr(T, "WAIT_POLL_SECONDS", 0)
    monkeypatch.setattr(T, "_wait_deadline", lambda _m: 0)
    data = json.loads((await run(ctx(WaitFake(["deploy"], []), 1), "timeweb_wait_deploy", app_id="5"))[0])
    assert data["finished"] is False and "note" in data


async def test_wait_database_until_public_ip(app_env, monkeypatch):
    monkeypatch.setattr(T, "WAIT_POLL_SECONDS", 0)
    uid = await _user()
    tw = WaitFake([], [("creating", None), ("creating", None), ("started", "5.129.242.60")])
    out, err = await run(ctx(tw, uid), "timeweb_wait_database", database_id=777)
    data = json.loads(out)
    assert not err and data["ready"] and data["public_ip"] == "5.129.242.60"


class IpFake(DbFake):
    """База запускается только с локальной сетью. public_network_works=False — флаг публичной сети не помогает
    (проверяем запасной путь через плавающий IP)."""

    def __init__(self, public_network_works=True, bound_already=False, connect_rejected=False):
        super().__init__(db_status="started", ip=None)
        self.public_network_works = public_network_works
        self.connect_rejected = connect_rejected
        self.fips = [{"id": "f0", "resource_type": "database", "resource_id": 777, "availability_zone": "msk-1"}] if bound_already else []
        self.db_patches, self.created_fips, self.binds, self.regrants = [], [], [], []

    async def request(self, method, path, **kw):
        if path == "/api/v1/databases/777" and method == "PATCH":
            self.db_patches.append(kw["json"])
            if self.public_network_works:
                self.ip = "5.129.242.61"
            return {}
        if path == "/api/v1/databases/777":
            body = await super().request(method, path, **kw)
            body["db"]["availability_zone"] = "msk-1"
            if self.binds:
                self.ip = "5.129.242.62"
            return body
        if path == "/api/v1/databases/777/admins/31" and method == "PATCH":
            if self.connect_rejected and "CONNECT" in kw["json"]["privileges"]:
                raise TimewebError(400, "privileges must be one of ...")
            self.regrants.append(kw["json"])
            return {}
        if path == "/api/v1/floating-ips" and method == "GET":
            return {"ips": self.fips, "meta": {}}
        if path == "/api/v1/floating-ips" and method == "POST":
            self.created_fips.append(kw["json"])
            return {"ip": {"id": "f1", "ip": "5.129.242.62", "availability_zone": kw["json"]["availability_zone"]}}
        if path == "/api/v1/floating-ips/f1/bind":
            self.binds.append(kw["json"])
            return {}
        return await super().request(method, path, **kw)


async def _agent_db(uid):
    from app import timeweb_db

    await timeweb_db.save(uid, 777, "shop-db", "postgres16", "app", "u1", "secret-pass")


async def test_wait_database_enables_public_network_and_regrants(app_env, monkeypatch):
    monkeypatch.setattr(T, "WAIT_POLL_SECONDS", 0)
    uid = await _user()
    await _agent_db(uid)
    tw = IpFake()
    out, err = await run(ctx(tw, uid), "timeweb_wait_database", database_id=777)
    data = json.loads(out)
    assert not err and data["ready"] and data["public_ip"] == "5.129.242.61"
    assert tw.db_patches == [{"is_enabled_public_network": True}] and tw.created_fips == []
    assert data["privileges_regranted"] is True
    assert tw.regrants == [{"privileges": T.REGRANT_PRIVILEGES["postgres"], "instance_id": 41}]


async def test_wait_database_falls_back_to_floating_ip(app_env, monkeypatch):
    monkeypatch.setattr(T, "WAIT_POLL_SECONDS", 0)
    monkeypatch.setattr(T, "PUBLIC_IP_FALLBACK_SECONDS", 0)
    uid = await _user()
    await _agent_db(uid)
    tw = IpFake(public_network_works=False)
    data = json.loads((await run(ctx(tw, uid), "timeweb_wait_database", database_id=777))[0])
    assert data["ready"] and data["public_ip"] == "5.129.242.62"
    assert tw.created_fips == [{"is_ddos_guard": False, "availability_zone": "msk-1"}]
    assert tw.binds == [{"resource_type": "database", "resource_id": 777}]


async def test_regrant_retries_without_connect_if_rejected(app_env, monkeypatch):
    monkeypatch.setattr(T, "WAIT_POLL_SECONDS", 0)
    uid = await _user()
    await _agent_db(uid)
    tw = IpFake(connect_rejected=True)
    data = json.loads((await run(ctx(tw, uid), "timeweb_wait_database", database_id=777))[0])
    assert data["privileges_regranted"] is True and "CONNECT" not in tw.regrants[0]["privileges"]


async def test_connect_always_prepares_database_first(app_env, monkeypatch):
    """Даже если агент пропустил ожидание, подключение к приложению включает IP и выдаёт права."""
    monkeypatch.setattr(T, "WAIT_POLL_SECONDS", 0)
    uid = await _user()
    await _agent_db(uid)
    tw = IpFake()
    tw.app_envs = {}
    out, err = await run(ctx(tw, uid), "timeweb_connect_database", app_id="5", database_id=777, redeploy=False)
    assert not err, out
    assert tw.db_patches and tw.regrants
    assert "5.129.242.61" in tw.patches[0]["envs"]["DATABASE_URL"]


async def test_wait_database_does_not_pay_for_foreign_database(app_env, monkeypatch):
    monkeypatch.setattr(T, "WAIT_POLL_SECONDS", 0)
    uid = await _user()
    tw = IpFake()
    data = json.loads((await run(ctx(tw, uid), "timeweb_wait_database", database_id=777))[0])
    assert not data["ready"] and "timeweb_fix_database_access" in data["note"]
    assert tw.db_patches == [] and tw.created_fips == []


async def test_fix_access_needs_confirmation_only_for_foreign_db_and_no_duplicate_ip(app_env, monkeypatch):
    monkeypatch.setattr(T, "WAIT_POLL_SECONDS", 0)
    monkeypatch.setattr(T, "PUBLIC_IP_FALLBACK_SECONDS", 0)
    uid = await _user()
    tw = IpFake()
    out, err = await run(ctx(tw, uid), "timeweb_fix_database_access", database_id=777)
    assert err and not tw.db_patches
    out, err = await run(ctx(tw, uid), "timeweb_fix_database_access", database_id=777, confirmed=True)
    assert not err and json.loads(out)["ready"]

    # плавающий IP уже привязан, но у базы ещё не виден — второй не создаём
    monkeypatch.setattr(T, "_wait_deadline", lambda _m: 0)
    tw = IpFake(public_network_works=False, bound_already=True)
    await run(ctx(tw, uid), "timeweb_fix_database_access", database_id=777, confirmed=True)
    assert tw.created_fips == []


async def test_wait_deploy_hints_at_privileges_bug(app_env, monkeypatch):
    monkeypatch.setattr(T, "WAIT_POLL_SECONDS", 0)
    tw = WaitFake(["failure"], [])

    async def logs(app_id):
        return ['psycopg.OperationalError: User does not have CONNECT privilege']

    tw.app_logs = logs
    data = json.loads((await run(ctx(tw, 1), "timeweb_wait_deploy", app_id="5", deploy_id="d2"))[0])
    assert "timeweb_fix_database_access" in data["hint"]
