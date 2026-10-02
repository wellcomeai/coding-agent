"""Комментатор: лёгкая модель пишет статусы, не влияя на агента и его историю."""

import asyncio
import json

import httpx
from sqlalchemy import select

from app import config, github_app
from app.agent import narrator as N
from app.agent.llm import LLMClient, LLMResult, Usage, set_llm, set_narrator_llm
from app.agent.runner import runner
from app.db import session_factory
from app.events import bus
from app.models import AgentSession, LedgerEntry, SessionEvent
from app.security import SESSION_COOKIE, sign_session

from test_agent_e2e import ScriptedLLM, call, make_user


class SlowScriptedLLM(ScriptedLLM):
    """Агент «думает» над шагом, пока комментатор успевает ответить."""

    async def complete(self, model, messages, tools, on_delta=None, **opts):
        await asyncio.sleep(0.05)
        return await super().complete(model, messages, tools, on_delta)


class FakeNarratorLLM(LLMClient):
    def __init__(self, replies=None, delay=0.0):
        self.replies = list(replies or [])
        self.delay = delay
        self.calls = []

    async def complete(self, model, messages, tools, on_delta=None, **opts):
        self.calls.append({"model": model, "messages": messages, "opts": opts})
        await asyncio.sleep(self.delay)
        text = self.replies.pop(0) if self.replies else "Работаю над задачей."
        for word in text.split(" "):
            await on_delta(word + " ")
        return LLMResult(content=text, usage=Usage(200, 20))


async def test_narrator_streams_status_records_milestones_and_keeps_agent_history_clean(app_env, monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("NARRATOR_MODEL", "deepseek/deepseek-v4-flash")
    config.get_settings.cache_clear()
    user = await make_user()
    agent = SlowScriptedLLM(
        [
            ("", [call("c1", "write_file", path="hello.py", content="print('hi')\n")]),
            ("", [call("c2", "git_commit_and_push", message="Add hello.py")]),
            ("Готово.", []),
        ]
    )
    narrator = FakeNarratorLLM(["Создаю hello.py по задаче.", "Пушу изменения в рабочую ветку."])
    set_llm(agent)
    set_narrator_llm(narrator)

    async def fake_repos(token):
        return [{"full_name": "owner/repo", "private": True, "default_branch": "main", "installation_id": 7,
                 "permissions": {"push": True}}]

    async def fake_user_token(db, user):
        return "ghu_user"

    monkeypatch.setattr(github_app, "list_user_repos", fake_repos)
    monkeypatch.setattr("app.routers.sessions.user_github_token", fake_user_token)

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(SESSION_COOKIE, sign_session(user.id))
        me = (await client.get("/api/auth/me")).json()
        assert me["agent_model"] == "deepseek/deepseek-v4-pro" and "models" not in me
        # модель из запроса игнорируется: связка фиксированная
        sid = (await client.post("/api/sessions", json={"repo_full_name": "owner/repo", "model": "anthropic/claude-sonnet-5"})).json()["id"]
        q = bus.subscribe(sid)
        assert (await client.post(f"/api/sessions/{sid}/messages", json={"text": "Добавь hello.py"})).status_code == 200
        await runner.wait(sid)

    live = []
    while not q.empty():
        live.append(q.get_nowait())
    bus.unsubscribe(sid, q)
    statuses = [e["data"]["text"] for e in live if e["type"] == "narration"]
    # сначала мгновенный статус по инструменту, затем уточнение от лёгкой модели
    assert statuses[:2] == ["Создаю hello.py", "Создаю hello.py по задаче."]
    assert "Пушу изменения в рабочую ветку." in statuses
    assert any(e["type"] == "narration_delta" for e in live)

    call0 = narrator.calls[0]
    assert call0["model"] == "deepseek/deepseek-v4-flash"
    assert call0["opts"]["stream"] is True and call0["opts"]["extra_body"] == {"reasoning_effort": "none"}
    assert "Добавь hello.py" in call0["messages"][1]["content"]

    async with session_factory()() as db:
        events = (await db.execute(select(SessionEvent).where(SessionEvent.session_id == sid))).scalars().all()
        types = [e.type for e in events]
        # живые статусы не сохраняются, веха (пуш) — сохраняется
        assert "narration" not in types and "narration_delta" not in types
        assert [e.data["text"] for e in events if e.type == "progress"] == ["Пушу изменения в рабочую ветку."]
        sess = await db.get(AgentSession, sid)
        assert sess.model == "deepseek/deepseek-v4-pro"
        assert "Пушу изменения" not in sess.history_json and "Создаю hello.py по" not in sess.history_json
        usage = (await db.execute(select(LedgerEntry).where(LedgerEntry.kind == "usage"))).scalars().all()
        narr = [u for u in usage if u.meta.get("role") == "narrator"]
        assert len(narr) == 2 and all(u.meta["model"] == "deepseek/deepseek-v4-flash" for u in narr)
        assert sess.cost_micro == -sum(u.amount_micro for u in usage)

    # агент не видел ни одного статуса комментатора
    assert "Пушу изменения" not in json.dumps(agent.seen, ensure_ascii=False)
    set_llm(None)
    set_narrator_llm(None)
    await runner.release_sandbox(sess, autosave=False)


async def test_narrator_comments_only_latest_step_and_skips_dash(app_env, monkeypatch):
    monkeypatch.setenv("NARRATOR_MODEL", "m")
    config.get_settings.cache_clear()
    user = await make_user()
    fake = FakeNarratorLLM(["Первый.", "-"], delay=0.05)
    set_narrator_llm(fake)
    q = bus.subscribe("s")
    n = N.Narrator("s", user.id, "задача")
    step = lambda name: [{"name": name, "args": "{}", "output": "ok"}]  # noqa: E731
    n.step_done("", step("grep"))
    await asyncio.sleep(0.01)
    n.step_done("", step("list_files"))
    n.step_done("", step("read_file"))  # заменяет list_files: в очередь копится только последний шаг
    await asyncio.sleep(0.2)
    await n.close()
    bus.unsubscribe("s", q)

    assert [json.loads(c["messages"][1]["content"].split("агента:\n")[1])["tools"][0]["name"] for c in fake.calls] == ["grep", "read_file"]
    texts = []
    while not q.empty():
        e = q.get_nowait()
        if e["type"] == "narration":
            texts.append(e["data"]["text"])
    assert texts == ["Первый."]  # ответ «-» не показываем
    set_narrator_llm(None)


async def test_narrator_failure_does_not_break_anything(app_env, monkeypatch):
    monkeypatch.setenv("NARRATOR_MODEL", "m")
    config.get_settings.cache_clear()

    class Broken(LLMClient):
        async def complete(self, *a, **k):
            raise RuntimeError("gateway down")

    set_narrator_llm(Broken())
    n = N.Narrator("s2", 1, "задача")
    n.step_done("", [{"name": "bash", "args": "{}", "output": "ok"}])
    await asyncio.sleep(0.05)
    await n.close()
    set_narrator_llm(None)


def test_tool_label():
    assert N.tool_label("read_file", '{"path": "src/app/main.py"}') == "Читаю main.py"
    assert N.tool_label("bash", "not json") == "Выполняю команду"
    assert N.tool_label("some_new_tool", "{}") == "Работаю: some new tool"
