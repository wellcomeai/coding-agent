import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import billing, github_app
from ..agent.runner import runner
from ..config import get_settings
from ..db import get_db, session_factory
from ..deps import current_user, user_github_token
from ..events import bus
from ..models import AgentSession, SessionEvent, User

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class CreateSession(BaseModel):
    repo_full_name: str
    base_branch: str | None = None
    model: str | None = None
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
    model = body.model or settings.default_model
    if model not in settings.models:
        raise HTTPException(400, f"Модель недоступна: {model}")
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
    sess = AgentSession(
        id=sid,
        user_id=user.id,
        repo_full_name=repo["full_name"],
        installation_id=repo["installation_id"],
        base_branch=body.base_branch or repo["default_branch"],
        work_branch=f"agent/{sid[:8]}",
        model=model,
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
    await runner.release_sandbox(sess)
    await db.execute(delete(SessionEvent).where(SessionEvent.session_id == session_id))
    await db.delete(sess)
    await db.commit()
    return {"ok": True}


def _sse(event: dict) -> str:
    head = f"id: {event['seq']}\n" if event.get("seq") else ""
    return head + "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


@router.get("/{session_id}/events")
async def events(
    session_id: str, request: Request, after: int = 0, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    await _get_owned(db, session_id, user)
    last_id = request.headers.get("last-event-id")
    if last_id and last_id.isdigit():
        after = max(after, int(last_id))

    async def stream():
        q = bus.subscribe(session_id)
        try:
            last = after
            async with session_factory()() as s2:  # отдельная сессия: основная закрывается после ответа
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
