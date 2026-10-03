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
