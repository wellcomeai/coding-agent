import asyncio
import json
import shlex
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import billing, github_app
from ..agent.prompts import NEW_BRANCH_NOTE
from ..agent.runner import START_REF, runner
from ..config import get_settings
from ..db import get_db, session_factory
from ..deps import current_user, user_github_token
from ..events import bus
from ..models import AgentSession, SessionEvent, User
from ..sandbox import get_provider
from ..security import SESSION_COOKIE, read_session

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class CreateSession(BaseModel):
    repo_full_name: str
    base_branch: str | None = None
    # True — агент создаёт новую ветку от base_branch; иначе работает прямо в base_branch
    new_branch: bool = False
    model: str | None = None  # устарело, игнорируется
    message: str | None = Field(default=None, max_length=50_000)


class SendMessage(BaseModel):
    text: str = Field(min_length=1, max_length=50_000)


def serialize(s: AgentSession, running: bool | None = None) -> dict:
    return {
        "id": s.id,
        "title": s.title,
        "repo_full_name": s.repo_full_name,
        "base_branch": s.base_branch,
        "work_branch": s.work_branch,
        "same_branch": s.work_branch == s.base_branch,
        "model": s.model,
        "status": "running" if running else s.status,
        "pr_url": s.pr_url,
        "cost_rub": billing.micro_to_rub(s.cost_micro),
        "created_at": s.created_at.isoformat(),
        "last_activity_at": s.last_activity_at.isoformat(),
        "branch_url": f"https://github.com/{s.repo_full_name}/tree/{s.work_branch}",
    }


async def _get_owned(db: AsyncSession, session_id: str, user: User) -> AgentSession:
    s = await db.get(AgentSession, session_id)
    if not s or s.user_id != user.id:
        raise HTTPException(404, "Сессия не найдена")
    return s


@router.get("")
async def list_sessions(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(AgentSession).where(AgentSession.user_id == user.id).order_by(AgentSession.last_activity_at.desc()).limit(100)
        )
    ).scalars().all()
    return {"sessions": [serialize(s, runner.is_running(s.id)) for s in rows]}


@router.post("")
async def create_session(body: CreateSession, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    model = settings.agent_model  # выбора модели нет: агент всегда работает на одной связке
    token = await user_github_token(db, user)
    repos = await github_app.list_user_repos(token)
    repo = next((r for r in repos if r["full_name"].lower() == body.repo_full_name.lower()), None)
    if not repo:
        raise HTTPException(403, "Нет доступа к репозиторию. Установите GitHub App и выдайте доступ к нему.")
    if not repo.get("permissions", {}).get("push", True):
        raise HTTPException(403, "У вас нет прав на запись в этот репозиторий")
    if body.message and user.balance_micro <= 0:
        raise HTTPException(402, "Недостаточно средств на балансе")
    sid = str(uuid.uuid4())
    base_branch = body.base_branch or repo["default_branch"]
    sess = AgentSession(
        id=sid,
        user_id=user.id,
        repo_full_name=repo["full_name"],
        installation_id=repo["installation_id"],
        base_branch=base_branch,
        # Агент работает в выбранной ветке; новую создаёт инструментом create_branch, если попросили
        work_branch=base_branch,
        model=model,
        history_json=json.dumps([{"role": "user", "content": NEW_BRANCH_NOTE}] if body.new_branch else [], ensure_ascii=False),
    )
    db.add(sess)
    await db.commit()
    if body.message:
        await runner.start_turn(sid, body.message)
    return serialize(sess, runner.is_running(sid))


@router.get("/{session_id}")
async def get_session(session_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return serialize(await _get_owned(db, session_id, user), runner.is_running(session_id))


@router.post("/{session_id}/messages")
async def send_message(
    session_id: str, body: SendMessage, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    await _get_owned(db, session_id, user)
    if user.balance_micro <= 0:
        raise HTTPException(402, "Недостаточно средств на балансе")
    try:
        await runner.start_turn(session_id, body.text)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/{session_id}/stop")
async def stop(session_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _get_owned(db, session_id, user)
    return {"stopped": await runner.stop(session_id)}


@router.delete("/{session_id}")
async def delete_session(session_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    sess = await _get_owned(db, session_id, user)
    await runner.stop(session_id)
    # WIP сохраняем только в отдельную ветку сессии: при работе прямо в выбранной ветке он попал бы
    # во временную ветку автосохранения, которую после удаления чата уже никто не восстановит
    await runner.release_sandbox(sess, autosave=sess.work_branch != sess.base_branch)
    await db.execute(delete(SessionEvent).where(SessionEvent.session_id == session_id))
    await db.delete(sess)
    await db.commit()
    return {"ok": True}


def _sse(event: dict) -> str:
    head = f"id: {event['seq']}\n" if event.get("seq") else ""
    return head + "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


@router.get("/{session_id}/events")
async def events(session_id: str, request: Request, after: int = 0):
    # Без Depends(get_db): иначе соединение из пула держалось бы всё время жизни SSE-потока
    uid = read_session(request.cookies.get(SESSION_COOKIE))
    async with session_factory()() as db:
        sess = await db.get(AgentSession, session_id)
    if not uid:
        raise HTTPException(401, "Требуется вход через GitHub")
    if not sess or sess.user_id != uid:
        raise HTTPException(404, "Сессия не найдена")
    last_id = request.headers.get("last-event-id")
    if last_id and last_id.isdigit():
        after = max(after, int(last_id))

    async def stream():
        q = bus.subscribe(session_id)
        try:
            last = after
            async with session_factory()() as s2:
                rows = (
                    await s2.execute(
                        select(SessionEvent)
                        .where(SessionEvent.session_id == session_id, SessionEvent.seq > after)
                        .order_by(SessionEvent.seq)
                    )
                ).scalars().all()
            for r in rows:
                last = r.seq
                yield _sse({"type": r.type, "data": r.data, "seq": r.seq, "ts": r.created_at.isoformat()})
            yield _sse({"type": "ready", "data": {"running": runner.is_running(session_id)}, "seq": None})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                if ev.get("seq") is not None:
                    if ev["seq"] <= last:
                        continue
                    last = ev["seq"]
                yield _sse(ev)
        finally:
            bus.unsubscribe(session_id, q)

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.get("/{session_id}/changes")
async def changes(session_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Изменения рабочей ветки относительно базовой (включая незакоммиченные), по файлам."""
    sess = await _get_owned(db, session_id, user)
    sb = await get_provider().get(sess.sandbox_id) if sess.sandbox_id else None
    if not sb:
        return {"available": False, "files": []}
    if sess.work_branch == sess.base_branch:
        # Агент пушит прямо в выбранную ветку: сравниваем с коммитом, с которого начата работа
        base = f"$(git rev-parse -q --verify {START_REF} || echo {shlex.quote(f'origin/{sess.base_branch}')})"
    else:
        base = shlex.quote(f"origin/{sess.base_branch}")
    numstat = await sb.exec(f"git add -A -N . && git diff --numstat {base}", timeout=60)
    patch = await sb.exec(f"git diff --no-color {base} | head -c 400000", timeout=60)
    if numstat.exit_code != 0:
        return {"available": False, "files": []}
    stats = {}
    for line in numstat.output.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            add, rem, path = parts
            stats[path] = (int(add) if add.isdigit() else 0, int(rem) if rem.isdigit() else 0)
    files = []
    for chunk in ("\n" + patch.output).split("\ndiff --git ")[1:]:
        header = chunk.split("\n", 1)[0]
        path = header.split(" b/", 1)[-1] if " b/" in header else header
        add, rem = stats.get(path, (0, 0))
        files.append({"path": path, "additions": add, "deletions": rem, "patch": "diff --git " + chunk})
    return {"available": True, "files": files}
