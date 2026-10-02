"""Полный ход агента: клон → правка файлов → bash → commit+push → итоговый ответ, со списанием баланса."""

import json
import subprocess

import httpx
from sqlalchemy import select

from app import billing, github_app
from app.agent.llm import LLMClient, LLMResult, Usage, set_llm
from app.agent.runner import runner
from app.db import session_factory
from app.models import AgentSession, LedgerEntry, SessionEvent, User
from app.security import SESSION_COOKIE, sign_session


def call(id_, name, **args):
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class ScriptedLLM(LLMClient):
    def __init__(self, steps):
        self.steps = list(steps)
        self.seen = []

    async def complete(self, model, messages, tools, on_delta=None):
        self.seen.append(messages)
        content, calls = self.steps.pop(0)
        if content and on_delta:
            await on_delta(content)
        return LLMResult(content=content, tool_calls=calls, usage=Usage(1000, 200))


async def make_user(balance_rub=100.0) -> User:
    async with session_factory()() as db:
        u = User(github_id=1, login="alice", balance_micro=billing.rub_to_micro(balance_rub))
        db.add(u)
        await db.commit()
        return u


async def test_full_agent_turn(app_env, monkeypatch):
    from app.main import create_app

    user = await make_user()
    llm = ScriptedLLM(
        [
            ("Создам файл.", [call("c1", "write_file", path="hello.py", content="print('hi')\n")]),
            ("", [call("c2", "edit_file", path="hello.py", old_string="hi", new_string="hello")]),
            ("", [call("c3", "bash", command="python3 hello.py"), call("c4", "read_file", path="../../etc/passwd")]),
            ("", [call("c5", "git_commit_and_push", message="Add hello.py")]),
            ("Готово: добавил hello.py.", []),
        ]
    )
    set_llm(llm)

    async def fake_repos(token):
        return [{"full_name": "owner/repo", "private": True, "default_branch": "main", "installation_id": 7,
                 "permissions": {"push": True}}]

    async def fake_user_token(db, user):
        return "ghu_user"

    monkeypatch.setattr(github_app, "list_user_repos", fake_repos)
    monkeypatch.setattr("app.routers.sessions.user_github_token", fake_user_token)

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(SESSION_COOKIE, sign_session(user.id))
        me = (await client.get("/api/auth/me")).json()
        assert me["login"] == "alice" and me["balance_rub"] == 100.0

        r = await client.post("/api/sessions", json={"repo_full_name": "owner/repo", "message": "Добавь hello.py"})
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        await runner.wait(sid)

        # второй ход нельзя запустить, пока идёт первый — но первый уже завершён
        detail = (await client.get(f"/api/sessions/{sid}")).json()
        assert detail["status"] == "idle"
        assert detail["cost_rub"] > 0

    # в «GitHub» появилась рабочая ветка с файлом
    branch = f"agent/{sid[:8]}"
    out = subprocess.run(["git", "--git-dir", str(app_env["bare"]), "show", f"{branch}:hello.py"],
                         capture_output=True, text=True, check=True).stdout
    assert out == "print('hello')\n"

    async with session_factory()() as db:
        events = (await db.execute(select(SessionEvent).where(SessionEvent.session_id == sid)
                                   .order_by(SessionEvent.seq))).scalars().all()
        types = [e.type for e in events]
        assert types[0] == "user_message"
        assert "tool_result" in types and types[-1] == "status"
        results = {e.data["id"]: e.data for e in events if e.type == "tool_result"}
        assert results["c3"]["output"].strip() == "hello"
        assert results["c4"]["is_error"] and "вне репозитория" in results["c4"]["output"]
        assert "ghs_FAKE_TOKEN_123" not in json.dumps([e.data for e in events])
        usage = (await db.execute(select(LedgerEntry).where(LedgerEntry.kind == "usage"))).scalars().all()
        assert len(usage) == 5
        u = await db.get(User, user.id)
        assert u.balance_micro == billing.rub_to_micro(100) + sum(e.amount_micro for e in usage)
        sess = await db.get(AgentSession, sid)
        history = json.loads(sess.history_json)
        assert history[0] == {"role": "user", "content": "Добавь hello.py"}
        assert history[-1]["content"] == "Готово: добавил hello.py."

    # системный промпт не содержит секретов, содержит рабочую ветку
    assert branch in llm.seen[0][0]["content"]
    set_llm(None)
    await runner.release_sandbox(sess, autosave=False)


async def test_zero_balance_blocks(app_env):
    from app.main import create_app

    user = await make_user(balance_rub=0)
    async with session_factory()() as db:
        db.add(AgentSession(id="s1", user_id=user.id, repo_full_name="owner/repo", installation_id=1,
                            base_branch="main", work_branch="agent/s1", model="m"))
        await db.commit()
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(SESSION_COOKIE, sign_session(user.id))
        r = await client.post("/api/sessions/s1/messages", json={"text": "привет"})
        assert r.status_code == 402


async def test_auth_required(app_env):
    from app.main import create_app

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/sessions")).status_code == 401
        client.cookies.set(SESSION_COOKIE, "forged")
        assert (await client.get("/api/auth/me")).status_code == 401


async def test_events_stream_access(app_env):
    from app.main import create_app

    user = await make_user()
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/sessions/nope/events")).status_code == 401
        client.cookies.set(SESSION_COOKIE, sign_session(user.id))
        assert (await client.get("/api/sessions/nope/events")).status_code == 404
