"""Цикл агента: песочница + LLM + инструменты + списание баланса + события для UI."""

import asyncio
import json
import logging
import shlex
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from .. import billing
from ..config import get_settings
from ..db import session_factory
from ..events import bus
from ..models import AgentSession, User
from ..sandbox import Sandbox, get_provider
from ..security import decrypt
from ..timeweb import TimewebClient
from . import tools as T
from .llm import get_llm
from .prompts import build_system_prompt

log = logging.getLogger(__name__)

HISTORY_CHAR_BUDGET = 350_000
KEEP_RECENT = 8


def compact_history(history: list[dict], budget: int = HISTORY_CHAR_BUDGET) -> list[dict]:
    """Укорачивает старые результаты инструментов, если история не влезает в бюджет."""

    def size(m: dict) -> int:
        return len(str(m.get("content") or "")) + len(json.dumps(m.get("tool_calls") or []))

    total = sum(size(m) for m in history)
    if total <= budget:
        return history
    for m in history[: max(0, len(history) - KEEP_RECENT)]:
        if total <= budget:
            break
        if m.get("role") == "tool" and len(m.get("content") or "") > 600:
            before = size(m)
            m["content"] = m["content"][:300] + "\n… [старый вывод сокращён] …\n" + m["content"][-200:]
            total -= before - size(m)
    return history


def close_dangling_tool_calls(history: list[dict]) -> None:
    """Добавляет ответы на вызовы инструментов, прерванные остановкой, чтобы история оставалась валидной."""
    answered = {m.get("tool_call_id") for m in history if m.get("role") == "tool"}
    for i, m in enumerate(list(history)):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                if tc["id"] not in answered:
                    history.append({"role": "tool", "tool_call_id": tc["id"], "content": "Прервано пользователем"})
                    answered.add(tc["id"])


class AgentRunner:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._sandbox_locks: dict[str, asyncio.Lock] = {}

    def is_running(self, session_id: str) -> bool:
        t = self._tasks.get(session_id)
        return bool(t and not t.done())

    async def start_turn(self, session_id: str, text: str) -> None:
        if self.is_running(session_id):
            raise RuntimeError("Агент уже работает в этой сессии")
        await bus.publish(session_id, "user_message", {"text": text})
        task = asyncio.create_task(self._turn(session_id, text))
        self._tasks[session_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(session_id, None) if self._tasks.get(session_id) is _t else None)

    async def stop(self, session_id: str) -> bool:
        t = self._tasks.get(session_id)
        if t and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            return True
        return False

    async def wait(self, session_id: str) -> None:
        t = self._tasks.get(session_id)
        if t:
            await asyncio.gather(t, return_exceptions=True)

    # ---------------- песочница ----------------

    async def ensure_sandbox(self, sess: AgentSession, user: User) -> Sandbox:
        lock = self._sandbox_locks.setdefault(sess.id, asyncio.Lock())
        async with lock:
            provider = get_provider()
            if sess.sandbox_id:
                sb = await provider.get(sess.sandbox_id)
                if sb:
                    return sb
            await bus.publish(sess.id, "status", {"status": "running", "detail": "Подготовка окружения и клонирование репозитория…"})
            sb = await provider.create(sess.id)
            try:
                await self._clone(sb, sess, user)
            except Exception:
                await sb.destroy()
                raise
            async with session_factory()() as db:
                await db.execute(update(AgentSession).where(AgentSession.id == sess.id).values(sandbox_id=sb.id))
                await db.commit()
            sess.sandbox_id = sb.id
            return sb

    async def _clone(self, sb: Sandbox, sess: AgentSession, user: User) -> None:
        from .. import github_app

        token = await github_app.installation_token(sess.installation_id, sess.repo_full_name)
        auth = T.git_auth_args(token)
        url = f"{get_settings().github_web_url}/{sess.repo_full_name}.git"
        parent = sb.repo_dir.rsplit("/", 1)[0]
        q = shlex.quote
        cmd = (
            f"git {auth} clone -q {q(url)} {q(sb.repo_dir)} && cd {q(sb.repo_dir)} && "
            f"git config user.name {q(user.login)} && "
            f"git config user.email {q(f'{user.github_id}+{user.login}@users.noreply.github.com')} && "
            f"if git rev-parse -q --verify origin/{q(sess.work_branch)} >/dev/null; then "
            f"git checkout -q -B {q(sess.work_branch)} origin/{q(sess.work_branch)}; "
            f"else git checkout -q -B {q(sess.work_branch)} origin/{q(sess.base_branch)}; fi"
        )
        res = await sb.exec(cmd, timeout=900, workdir=parent)
        if res.exit_code != 0:
            raise RuntimeError("Не удалось клонировать репозиторий:\n" + T.scrub(res.output, [token])[-2000:])

    # ---------------- ход агента ----------------

    async def _turn(self, session_id: str, text: str) -> None:
        s = get_settings()
        history: list[dict] = []
        sess: AgentSession | None = None
        try:
            async with session_factory()() as db:
                sess = await db.get(AgentSession, session_id)
                user = await db.get(User, sess.user_id) if sess else None
                if not sess or not user:
                    return
                sess.status = "running"
                sess.last_activity_at = datetime.now(UTC)
                if sess.title == "Новая сессия":
                    sess.title = text.strip().splitlines()[0][:80] or sess.title
                await db.commit()
                history = json.loads(sess.history_json or "[]")
            await bus.publish(session_id, "status", {"status": "running"})

            history.append({"role": "user", "content": text})
            await self._save_history(session_id, history)

            sandbox = await self.ensure_sandbox(sess, user)
            tw_token = decrypt(user.timeweb_token_enc)

            async def on_pr(url: str) -> None:
                async with session_factory()() as db:
                    await db.execute(update(AgentSession).where(AgentSession.id == session_id).values(pr_url=url))
                    await db.commit()
                await bus.publish(session_id, "pr", {"url": url})

            ctx = T.ToolContext(
                sandbox=sandbox,
                repo=sess.repo_full_name,
                installation_id=sess.installation_id,
                base_branch=sess.base_branch,
                work_branch=sess.work_branch,
                timeweb=TimewebClient(tw_token) if tw_token else None,
                pr_url=sess.pr_url,
                on_pr=on_pr,
                secrets=[tw_token] if tw_token else [],
            )
            specs, registry = T.toolset(with_timeweb=bool(tw_token))
            system = build_system_prompt(
                sess.repo_full_name, sandbox.repo_dir, sess.base_branch, sess.work_branch, bool(tw_token)
            )
            await billing.price_book.refresh()
            llm = get_llm()

            for _step in range(s.agent_max_steps):
                async with session_factory()() as db:
                    balance = await billing.get_balance(db, user.id)
                if balance <= 0:
                    await bus.publish(
                        session_id, "error", {"message": "Недостаточно средств на балансе. Пополните баланс, чтобы продолжить."}
                    )
                    break

                async def on_delta(chunk: str) -> None:
                    await bus.publish(session_id, "assistant_delta", {"text": chunk})

                messages = [{"role": "system", "content": system}] + compact_history(history)
                result = await llm.complete(sess.model, messages, specs, on_delta)

                cost = billing.price_book.cost_micro(
                    sess.model, result.usage.prompt_tokens, result.usage.completion_tokens, result.usage.cached_tokens
                )
                async with session_factory()() as db:
                    new_balance = await billing.add_entry(
                        db,
                        user.id,
                        -cost,
                        "usage",
                        session_id,
                        {
                            "model": sess.model,
                            "prompt_tokens": result.usage.prompt_tokens,
                            "completion_tokens": result.usage.completion_tokens,
                            "cached_tokens": result.usage.cached_tokens,
                            "estimated": result.usage.estimated,
                        },
                    )
                    await db.execute(
                        update(AgentSession)
                        .where(AgentSession.id == session_id)
                        .values(cost_micro=AgentSession.cost_micro + cost)
                    )
                    await db.commit()
                await bus.publish(
                    session_id,
                    "usage",
                    {
                        "cost_rub": billing.micro_to_rub(cost),
                        "balance_rub": billing.micro_to_rub(new_balance),
                        "prompt_tokens": result.usage.prompt_tokens,
                        "completion_tokens": result.usage.completion_tokens,
                    },
                )

                history.append(result.as_message())
                await self._save_history(session_id, history)
                if result.content:
                    await bus.publish(session_id, "assistant_message", {"text": result.content})
                if not result.tool_calls:
                    break

                for tc in result.tool_calls:
                    name = tc["function"]["name"]
                    args = tc["function"]["arguments"]
                    await bus.publish(session_id, "tool_call", {"id": tc["id"], "name": name, "arguments": args})
                    output, is_error = await T.execute(ctx, registry, name, args)
                    history.append({"role": "tool", "tool_call_id": tc["id"], "content": output})
                    await bus.publish(
                        session_id,
                        "tool_result",
                        {"id": tc["id"], "name": name, "output": T.truncate(output, 8000), "is_error": is_error},
                    )
                await self._save_history(session_id, history)
            else:
                await bus.publish(
                    session_id, "error", {"message": f"Достигнут лимит шагов ({s.agent_max_steps}). Напишите «продолжай»."}
                )
            await self._finish(session_id, "idle")
        except asyncio.CancelledError:
            close_dangling_tool_calls(history)
            history.append({"role": "user", "content": "[Пользователь остановил выполнение]"})
            await asyncio.shield(self._save_history(session_id, history))
            await asyncio.shield(bus.publish(session_id, "stopped", {}))
            await asyncio.shield(self._finish(session_id, "idle"))
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("Ошибка в сессии %s", session_id)
            close_dangling_tool_calls(history)
            if history:
                await self._save_history(session_id, history)
            await bus.publish(session_id, "error", {"message": f"Ошибка: {e}"[:2000]})
            await self._finish(session_id, "error")

    async def _save_history(self, session_id: str, history: list[dict]) -> None:
        async with session_factory()() as db:
            await db.execute(
                update(AgentSession)
                .where(AgentSession.id == session_id)
                .values(history_json=json.dumps(history, ensure_ascii=False), last_activity_at=datetime.now(UTC))
            )
            await db.commit()

    async def _finish(self, session_id: str, status: str) -> None:
        async with session_factory()() as db:
            await db.execute(
                update(AgentSession)
                .where(AgentSession.id == session_id)
                .values(status=status, last_activity_at=datetime.now(UTC))
            )
            await db.commit()
        await bus.publish(session_id, "status", {"status": status})

    # ---------------- обслуживание ----------------

    async def release_sandbox(self, sess: AgentSession, autosave: bool = True) -> None:
        """Удалить песочницу; незакоммиченные изменения сохраняются WIP-коммитом в рабочую ветку."""
        if not sess.sandbox_id:
            return
        provider = get_provider()
        sb = await provider.get(sess.sandbox_id)
        if sb:
            if autosave:
                try:
                    from .. import github_app

                    res = await sb.exec("git status --porcelain", timeout=60)
                    if res.exit_code == 0 and res.output.strip():
                        token = await github_app.installation_token(sess.installation_id, sess.repo_full_name)
                        await sb.exec(
                            "git add -A && git commit -q -m 'WIP: автосохранение Coding Agent' && "
                            f"git {T.git_auth_args(token)} push -q origin HEAD:refs/heads/{shlex.quote(sess.work_branch)}",
                            timeout=300,
                        )
                except Exception as e:  # noqa: BLE001
                    log.warning("Автосохранение сессии %s не удалось: %s", sess.id, e)
            await sb.destroy()
        async with session_factory()() as db:
            await db.execute(update(AgentSession).where(AgentSession.id == sess.id).values(sandbox_id=None))
            await db.commit()

    async def reap_idle(self) -> int:
        s = get_settings()
        cutoff = datetime.now(UTC) - timedelta(minutes=s.sandbox_idle_minutes)
        async with session_factory()() as db:
            rows = (
                await db.execute(
                    select(AgentSession).where(AgentSession.sandbox_id.is_not(None), AgentSession.status != "running")
                )
            ).scalars().all()
        n = 0
        for sess in rows:
            last = sess.last_activity_at if sess.last_activity_at.tzinfo else sess.last_activity_at.replace(tzinfo=UTC)
            if last < cutoff and not self.is_running(sess.id):
                await self.release_sandbox(sess)
                n += 1
        return n

    async def recover_after_restart(self) -> None:
        async with session_factory()() as db:
            await db.execute(update(AgentSession).where(AgentSession.status == "running").values(status="idle"))
            await db.commit()


runner = AgentRunner()
