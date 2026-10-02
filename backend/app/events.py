"""События сессии агента: сохранение в БД + живая рассылка подписчикам (SSE).

Шина в памяти процесса — для одного инстанса API. При горизонтальном масштабировании
заменяется на Redis Pub/Sub с тем же интерфейсом (publish/subscribe).
"""

import asyncio
from collections import defaultdict

from sqlalchemy import func, select

from .db import session_factory
from .models import SessionEvent

# События, которые не сохраняются в БД (только live-стриминг)
EPHEMERAL = {"assistant_delta", "narration", "narration_delta"}


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._seq_lock: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def subscribe(self, session_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=5000)
        self._subs[session_id].add(q)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue) -> None:
        self._subs[session_id].discard(q)
        if not self._subs[session_id]:
            self._subs.pop(session_id, None)

    def _fanout(self, session_id: str, event: dict) -> None:
        for q in list(self._subs.get(session_id, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def publish(self, session_id: str, type_: str, data: dict) -> dict:
        event = {"type": type_, "data": data, "seq": None}
        if type_ not in EPHEMERAL:
            async with self._seq_lock[session_id]:
                async with session_factory()() as db:
                    seq = (
                        await db.execute(
                            select(func.coalesce(func.max(SessionEvent.seq), 0)).where(
                                SessionEvent.session_id == session_id
                            )
                        )
                    ).scalar_one() + 1
                    db.add(SessionEvent(session_id=session_id, seq=seq, type=type_, data=data))
                    await db.commit()
            event["seq"] = seq
        self._fanout(session_id, event)
        return event


bus = EventBus()
