import re

from app.agent import tools as T
from app.timeweb import TimewebClient, TimewebError, repo_matches


class FakeTimeweb(TimewebClient):
    """Имитация API: провайдеры и репозитории, с постраничной выдачей или без неё."""

    def __init__(self, providers, repos, honor_offset=True, reject_params=False, fail=None):
        super().__init__("token")
        self.providers, self.repos = providers, repos
        self.honor_offset, self.reject_params, self.fail = honor_offset, reject_params, fail
        self.calls = []

    async def request(self, method, path, **kw):
        self.calls.append(path)
        if self.fail:
            raise TimewebError(self.fail, "Forbidden")
        if path == "/api/v1/vcs-provider":
            return {"providers": self.providers, "meta": {"total": len(self.providers)}}
        m = re.match(r"/api/v1/vcs-provider/([^?]+)(?:\?limit=(\d+)&offset=(\d+))?$", path)
        assert m, path
        items = self.repos[m.group(1)]
        if m.group(2):
            if self.reject_params:
                raise TimewebError(400, "bad params")
            limit, offset = int(m.group(2)), int(m.group(3)) if self.honor_offset else 0
            return {"repositories": items[offset : offset + limit], "meta": {"total": len(items)}}
        return {"repositories": items[:100], "meta": {"total": len(items)}}


GH = {"provider_id": "p1", "login": "SturtapHab", "provider_type": "github"}


def repo(i, name=None, **kw):
    return {"id": f"r{i}", "name": name or f"repo{i}", **kw}


def test_repo_matches_variants():
    assert repo_matches("SturtapHab/OpenSaas", {"full_name": "sturtaphab/opensaas"})
    assert repo_matches("SturtapHab/OpenSaas", {"name": "OpenSaas"}, "SturtapHab")
    assert repo_matches("SturtapHab/OpenSaas", {"name": "x", "url": "https://github.com/SturtapHab/OpenSaas.git"})
    assert not repo_matches("SturtapHab/OpenSaas", {"name": "OpenSaas"}, "someone-else")
    assert not repo_matches("SturtapHab/OpenSaas", {"full_name": "SturtapHab/Other"})


async def test_finds_repo_by_short_name_on_second_page():
    repos = [repo(i) for i in range(150)]
    repos[120] = repo(120, "OpenSaas")  # только короткое имя, без full_name
    tw = FakeTimeweb([GH], {"p1": repos})
    lookup = await tw.diagnose_repository("SturtapHab/OpenSaas")
    assert lookup.match and lookup.match[1]["id"] == "r120"
    assert lookup.providers[0]["repos_count"] == 150


async def test_api_ignoring_offset_does_not_loop_forever():
    tw = FakeTimeweb([GH], {"p1": [repo(i) for i in range(250)]}, honor_offset=False)
    lookup = await tw.diagnose_repository("SturtapHab/OpenSaas")
    assert lookup.match is None and len(tw.calls) <= 4
    text = lookup.summary()
    assert "SturtapHab" in text and "100 репоз." in text and "github.com/settings/installations" in text


async def test_falls_back_when_pagination_params_rejected():
    tw = FakeTimeweb([GH], {"p1": [repo(1, full_name="SturtapHab/OpenSaas")]}, reject_params=True)
    assert await tw.find_repository("SturtapHab/OpenSaas")


async def test_no_providers_message():
    lookup = await FakeTimeweb([], {}).diagnose_repository("SturtapHab/OpenSaas")
    assert lookup.match is None and "не подключён ни один GitHub-аккаунт" in lookup.summary()


async def test_tool_reports_found_and_api_errors(tmp_path):
    class SB:
        repo_dir = "/w/repo"

    def ctx(tw):
        return T.ToolContext(sandbox=SB(), repo="SturtapHab/OpenSaas", installation_id=1, base_branch="main",
                             work_branch="agent/x", timeweb=tw)

    ok = await T.t_timeweb_find_repository(ctx(FakeTimeweb([GH], {"p1": [repo(7, "OpenSaas")]})))
    assert '"connected": true' in ok and '"repository_id": "r7"' in ok

    _, registry = T.toolset(True)
    out, is_error = await T.execute(ctx(FakeTimeweb([GH], {}, fail=403)), registry, "timeweb_find_repository", "{}")
    assert is_error and "403" in out and "токен" in out


async def test_settings_status_endpoint(app_env, monkeypatch):
    import httpx

    from app.db import session_factory
    from app.main import create_app
    from app.models import User
    from app.security import SESSION_COOKIE, encrypt, sign_session

    class StatusFake(FakeTimeweb):
        def __init__(self, token):
            super().__init__([GH], {"p1": [repo(1, "OpenSaas"), repo(2)]})

        async def account_status(self):
            return {"login": "fy407598"}

    monkeypatch.setattr("app.routers.account.TimewebClient", StatusFake)
    async with session_factory()() as db:
        u = User(github_id=5, login="tw", balance_micro=0, timeweb_token_enc=encrypt("tok-123456789012345678"))
        db.add(u)
        await db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
        c.cookies.set(SESSION_COOKIE, sign_session(u.id))
        st = (await c.get("/api/settings/timeweb/status")).json()
    assert st["valid"] and st["account"] == "fy407598"
    assert st["providers"] == [{"login": "SturtapHab", "type": "github", "repos_count": 2}]
