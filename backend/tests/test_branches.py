"""Работа с веткой: по умолчанию агент пушит в выбранную ветку, новую создаёт только по просьбе."""

import json
import subprocess

import httpx

from app import github_app
from app.agent.llm import LLMClient, LLMResult, Usage, set_llm
from app.agent.runner import autosave_branch, runner
from app.db import session_factory
from app.models import AgentSession, SessionEvent
from app.security import SESSION_COOKIE, sign_session
from sqlalchemy import select

from test_agent_e2e import call, make_user


class StepLLM(LLMClient):
    """Как ScriptedLLM, но шаг может быть функцией — чтобы менять «GitHub» посреди хода."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.seen = []

    async def complete(self, model, messages, tools, on_delta=None):
        self.seen.append(messages)
        step = self.steps.pop(0)
        if callable(step):
            step = step()
        content, calls = step
        return LLMResult(content=content, tool_calls=calls, usage=Usage(10, 10))


def git_bare(app_env, *args) -> str:
    return subprocess.run(["git", "--git-dir", str(app_env["bare"]), *args], capture_output=True, text=True,
                          check=True).stdout


def heads(app_env) -> list[str]:
    return git_bare(app_env, "branch", "--format=%(refname:short)").split()


async def tool_results(sid) -> dict:
    async with session_factory()() as db:
        events = (await db.execute(select(SessionEvent).where(SessionEvent.session_id == sid))).scalars().all()
    return {e.data["id"]: e.data for e in events if e.type == "tool_result"}


def client_for(monkeypatch, user):
    from app.main import create_app

    async def fake_repos(token):
        return [{"full_name": "owner/repo", "private": True, "default_branch": "main", "installation_id": 7,
                 "permissions": {"push": True}}]

    async def fake_user_token(db, user_):
        return "ghu_user"

    monkeypatch.setattr(github_app, "list_user_repos", fake_repos)
    monkeypatch.setattr("app.routers.sessions.user_github_token", fake_user_token)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test")
    c.cookies.set(SESSION_COOKIE, sign_session(user.id))
    return c


async def test_new_branch_toggle_makes_agent_create_its_branch(app_env, monkeypatch):
    user = await make_user()
    llm = StepLLM(
        [
            ("", [call("c1", "write_file", path="a.txt", content="a\n"), call("c2", "git_commit_and_push", message="A")]),
            ("", [{"id": "c3", "type": "function", "function": {"name": "create_branch", "arguments": json.dumps({"name": "feature/add-a"})}}]),
            ("", [call("c4", "git_commit_and_push", message="A")]),
            ("Готово.", []),
        ]
    )
    set_llm(llm)
    async with client_for(monkeypatch, user) as client:
        r = await client.post("/api/sessions", json={"repo_full_name": "owner/repo", "message": "Добавь a.txt",
                                                     "new_branch": True})
        sid = r.json()["id"]
        assert r.json()["work_branch"] == "main"
        await runner.wait(sid)
        detail = (await client.get(f"/api/sessions/{sid}")).json()

    res = await tool_results(sid)
    assert res["c2"]["is_error"] and "create_branch" in res["c2"]["output"]  # в main пушить нельзя
    assert not res["c3"]["is_error"] and not res["c4"]["is_error"]
    assert "NEW branch" in llm.seen[0][0]["content"]
    assert detail["work_branch"] == "feature/add-a" and not detail["same_branch"]
    assert sorted(heads(app_env)) == ["feature/add-a", "main"]
    assert git_bare(app_env, "show", "feature/add-a:a.txt") == "a\n"
    assert "a.txt" not in git_bare(app_env, "ls-tree", "--name-only", "main")
    async with session_factory()() as db:
        sess = await db.get(AgentSession, sid)
    set_llm(None)
    await runner.release_sandbox(sess, autosave=False)


async def test_same_branch_push_rebases_and_refuses_pr(app_env, monkeypatch):
    user = await make_user()
    seed = app_env["tmp"] / "seed"

    def someone_pushes():
        (seed / "other.txt").write_text("other\n")
        git = ["git", "-C", str(seed), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git + ["add", "."], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "other"], check=True)
        subprocess.run(git + ["push", "-q", str(app_env["bare"]), "main"], check=True)
        return "", [call("c2", "git_commit_and_push", message="Mine")]

    set_llm(StepLLM(
        [
            ("", [call("c1", "write_file", path="mine.txt", content="mine\n")]),
            someone_pushes,
            ("", [call("c3", "create_pull_request", title="PR")]),
            ("Готово.", []),
        ]
    ))
    async with client_for(monkeypatch, user) as client:
        sid = (await client.post("/api/sessions", json={"repo_full_name": "owner/repo", "message": "x"})).json()["id"]
        await runner.wait(sid)

    res = await tool_results(sid)
    assert not res["c2"]["is_error"], res["c2"]["output"]
    assert res["c3"]["is_error"] and "create_branch" in res["c3"]["output"]
    files = git_bare(app_env, "ls-tree", "--name-only", "main").split()
    assert {"mine.txt", "other.txt"} <= set(files)  # чужой коммит сохранён, наш — поверх
    assert heads(app_env) == ["main"]
    async with session_factory()() as db:
        sess = await db.get(AgentSession, sid)
    set_llm(None)
    await runner.release_sandbox(sess, autosave=False)


async def test_autosave_never_pushes_wip_into_selected_branch(app_env, monkeypatch):
    user = await make_user()
    set_llm(StepLLM([("", [call("c1", "write_file", path="wip.txt", content="wip\n")]), ("Пауза.", [])]))
    async with client_for(monkeypatch, user) as client:
        sid = (await client.post("/api/sessions", json={"repo_full_name": "owner/repo", "message": "x"})).json()["id"]
        await runner.wait(sid)
        main_before = git_bare(app_env, "rev-parse", "main")
        async with session_factory()() as db:
            sess = await db.get(AgentSession, sid)
        await runner.release_sandbox(sess)
        backup = autosave_branch(sid)
        assert git_bare(app_env, "rev-parse", "main") == main_before
        assert git_bare(app_env, "show", f"{backup}:wip.txt") == "wip\n"

        # следующий ход: новая песочница возвращает незакоммиченную работу и убирает временную ветку
        llm = StepLLM([("", [call("c2", "bash", command="git status --porcelain && cat wip.txt")]), ("Ок.", [])])
        set_llm(llm)
        assert (await client.post(f"/api/sessions/{sid}/messages", json={"text": "продолжай"})).status_code == 200
        await runner.wait(sid)
        ch = (await client.get(f"/api/sessions/{sid}/changes")).json()

    out = (await tool_results(sid))["c2"]["output"]
    assert "wip.txt" in out and "wip" in out
    assert heads(app_env) == ["main"]
    assert [f["path"] for f in ch["files"]] == ["wip.txt"]
    async with session_factory()() as db:
        sess = await db.get(AgentSession, sid)
    set_llm(None)
    await runner.release_sandbox(sess, autosave=False)


async def test_deleting_chat_leaves_no_autosave_branch(app_env, monkeypatch):
    user = await make_user()
    set_llm(StepLLM([("", [call("c1", "write_file", path="wip.txt", content="wip\n")]), ("Ок.", [])]))
    async with client_for(monkeypatch, user) as client:
        sid = (await client.post("/api/sessions", json={"repo_full_name": "owner/repo", "message": "x"})).json()["id"]
        await runner.wait(sid)
        assert (await client.delete(f"/api/sessions/{sid}")).json() == {"ok": True}
        assert (await client.get(f"/api/sessions/{sid}")).status_code == 404
    set_llm(None)
    assert heads(app_env) == ["main"]


async def test_changes_survive_sandbox_recreation(app_env, monkeypatch):
    """«Изменения» считаются от исходного коммита сессии и после пересоздания песочницы."""
    user = await make_user()
    set_llm(StepLLM([("", [call("c1", "write_file", path="f.txt", content="f\n"),
                           call("c2", "git_commit_and_push", message="F")]), ("Ок.", [])]))
    async with client_for(monkeypatch, user) as client:
        sid = (await client.post("/api/sessions", json={"repo_full_name": "owner/repo", "message": "x"})).json()["id"]
        await runner.wait(sid)
        async with session_factory()() as db:
            sess = await db.get(AgentSession, sid)
        assert sess.start_sha and git_bare(app_env, "rev-parse", "main~1").strip() == sess.start_sha
        await runner.release_sandbox(sess)

        set_llm(StepLLM([("", [call("c3", "bash", command="true")]), ("Ок.", [])]))
        await client.post(f"/api/sessions/{sid}/messages", json={"text": "дальше"})
        await runner.wait(sid)
        ch = (await client.get(f"/api/sessions/{sid}/changes")).json()
    assert [f["path"] for f in ch["files"]] == ["f.txt"]
    async with session_factory()() as db:
        sess = await db.get(AgentSession, sid)
    set_llm(None)
    await runner.release_sandbox(sess, autosave=False)


async def test_list_branches_reads_every_page(monkeypatch):
    pages = {1: [f"b{i}" for i in range(100)], 2: [f"b{i}" for i in range(100, 200)], 3: ["last"]}
    seen = []

    async def fake_request(method, path, token=None, **kw):
        page = int(path.rsplit("page=", 1)[1])
        seen.append(page)
        return [{"name": n} for n in pages.get(page, [])]

    monkeypatch.setattr(github_app, "_request", fake_request)
    names = await github_app.list_branches("t", "o/r")
    assert len(names) == 201 and names[-1] == "last" and seen == [1, 2, 3]


async def test_sessions_search_paging_and_rename(app_env):
    from app.main import create_app

    user = await make_user()
    async with session_factory()() as db:
        for i in range(5):
            db.add(AgentSession(id=f"s{i}", user_id=user.id, repo_full_name="owner/repo", installation_id=1,
                                base_branch="main", work_branch="main", model="m", title=f"Задача {i}"))
        db.add(AgentSession(id="x", user_id=user.id, repo_full_name="owner/other", installation_id=1,
                            base_branch="main", work_branch="main", model="m", title="Логин 100%"))
        await db.commit()
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test")
    c.cookies.set(SESSION_COOKIE, sign_session(user.id))
    async with c as client:
        r = (await client.get("/api/sessions?limit=4")).json()
        assert len(r["sessions"]) == 4 and r["has_more"]
        r = (await client.get("/api/sessions?limit=10")).json()
        assert len(r["sessions"]) == 6 and not r["has_more"]
        assert [s["id"] for s in (await client.get("/api/sessions?q=other")).json()["sessions"]] == ["x"]
        assert [s["id"] for s in (await client.get("/api/sessions?q=100%")).json()["sessions"]] == ["x"]
        assert (await client.get("/api/sessions?q=%25%25%25")).json()["sessions"] == []

        r = await client.patch("/api/sessions/s1", json={"title": "  Новое   имя "})
        assert r.status_code == 200 and r.json()["title"] == "Новое имя"
        assert (await client.patch("/api/sessions/s1", json={"title": "   "})).status_code == 422
        assert (await client.patch("/api/sessions/nope", json={"title": "a"})).status_code == 404
        assert (await client.get("/api/sessions/s1")).json()["title"] == "Новое имя"
