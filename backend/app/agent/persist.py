"""Состояние песочницы, которое переживает её удаление.

Песочница удаляется после простоя, а скрипты проектов хранят нужное в $HOME (например,
deploy_timeweb.py держит пароли базы и админа в ~/.opensaas-timeweb). Без них не работают
set-env, test-email и другие команды в следующих сессиях.

После каждого хода файлы из SANDBOX_PERSIST_DIRS сохраняются в БД (зашифрованы), при создании
песочницы — восстанавливаются. Сохранение объединяет файлы с уже сохранёнными, а не заменяет их:
две параллельные сессии одного пользователя не затирают файлы друг друга.
"""

import base64
import io
import json
import logging
import shlex
import tarfile

from ..config import get_settings
from ..db import session_factory
from ..models import SandboxState
from ..sandbox import Sandbox
from ..security import decrypt, encrypt

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024


def _dirs() -> list[str]:
    return [d.strip().strip("/") for d in get_settings().sandbox_persist_dirs.split(",") if d.strip()]


async def _load(user_id: int) -> dict[str, str]:
    async with session_factory()() as db:
        row = await db.get(SandboxState, user_id)
        return json.loads(decrypt(row.files_enc) or "{}") if row else {}


async def save(sandbox: Sandbox, user_id: int) -> int:
    """Сохранить файлы из песочницы. Возвращает число сохранённых файлов."""
    dirs = _dirs()
    if not dirs:
        return 0
    listed = " ".join(shlex.quote(d) for d in dirs)
    res = await sandbox.exec(
        f'cd ~ && ls -d {listed} 2>/dev/null | xargs -r tar czf - 2>/dev/null | base64 -w0', timeout=60, workdir="/"
    )
    if res.exit_code != 0 or not res.output.strip():
        return 0
    found: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(res.output.strip())), mode="r:gz") as tar:
        for m in tar.getmembers():
            if m.isfile() and m.size <= MAX_FILE_BYTES and not m.name.startswith(("/", "..")):
                f = tar.extractfile(m)
                if f:
                    found[m.name] = base64.b64encode(f.read()).decode()
    if not found:
        return 0
    merged = {**await _load(user_id), **found}
    if sum(len(v) for v in merged.values()) * 3 // 4 > MAX_TOTAL_BYTES:
        log.warning("Состояние песочницы пользователя %s больше лимита, не сохраняю", user_id)
        return 0
    async with session_factory()() as db:
        row = await db.get(SandboxState, user_id)
        if row:
            row.files_enc = encrypt(json.dumps(merged))
        else:
            db.add(SandboxState(user_id=user_id, files_enc=encrypt(json.dumps(merged))))
        await db.commit()
    return len(found)


async def restore(sandbox: Sandbox, user_id: int) -> int:
    """Вернуть сохранённые файлы в $HOME новой песочницы. Возвращает число файлов."""
    files = await _load(user_id)
    if not files:
        return 0
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            raw = base64.b64decode(data)
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            info.mode = 0o600
            tar.addfile(info, io.BytesIO(raw))
    home = (await sandbox.exec("echo $HOME", timeout=30, workdir="/")).output.strip()
    archive = f"{home}/.agent-restore.tgz"
    await sandbox.write_file(archive, buf.getvalue())
    res = await sandbox.exec(f"cd ~ && tar xzf {shlex.quote(archive)} && rm -f {shlex.quote(archive)}", timeout=60, workdir="/")
    if res.exit_code != 0:
        log.warning("Не удалось восстановить состояние песочницы пользователя %s: %s", user_id, res.output[-500:])
        return 0
    return len(files)
