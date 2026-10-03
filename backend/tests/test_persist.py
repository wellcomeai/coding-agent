"""Состояние скриптов в $HOME переживает удаление песочницы."""

from app.agent import persist
from app.db import session_factory
from app.models import SandboxState, User
from app.sandbox import get_provider


async def _user() -> int:
    async with session_factory()() as db:
        u = User(github_id=5, login="bob", balance_micro=0)
        db.add(u)
        await db.commit()
        return u.id


async def test_state_survives_sandbox_and_merges_between_sessions(app_env):
    uid = await _user()
    provider = get_provider()

    a = await provider.create("s-a")
    b = await provider.create("s-b")
    await a.exec("mkdir -p ~/.opensaas-timeweb && echo '{\"db_password\": \"pw-Secret-42\"}' > ~/.opensaas-timeweb/app1.json "
                 "&& mkdir -p ~/junk && echo x > ~/junk/f", workdir="/")
    await b.exec("mkdir -p ~/.agent-state && echo note > ~/.agent-state/n.txt", workdir="/")
    assert await persist.save(a, uid) == 1  # ~/junk не сохраняется
    assert await persist.save(b, uid) == 1  # вторая сессия дописывает, а не затирает
    await a.destroy()
    await b.destroy()

    async with session_factory()() as db:
        row = await db.get(SandboxState, uid)
        assert "pw-Secret-42" not in row.files_enc  # хранится зашифрованным (короткое значение могло бы случайно встретиться в шифртексте)

    c = await provider.create("s-c")
    assert await persist.restore(c, uid) == 2
    out = (await c.exec("cat ~/.opensaas-timeweb/app1.json ~/.agent-state/n.txt; ls ~/junk 2>&1", workdir="/")).output
    assert '"db_password": "pw-Secret-42"' in out and "note" in out and "No such file" in out
    await c.destroy()


async def test_nothing_to_save_or_restore(app_env):
    uid = await _user()
    sb = await get_provider().create("s-empty")
    assert await persist.save(sb, uid) == 0
    assert await persist.restore(sb, uid) == 0
    await sb.destroy()
