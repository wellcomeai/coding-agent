"""Локальная «песочница» — каталог на диске хоста. НЕ изолирует! Только для разработки и тестов."""

import asyncio
import os
import shutil
import signal
import uuid

from ..config import get_settings
from .base import ExecResult, Sandbox, SandboxProvider


class LocalSandbox(Sandbox):
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.id = "local:" + self.root
        self.repo_dir = os.path.join(self.root, "repo")

    async def exec(self, command: str, timeout: int = 300, workdir: str | None = None) -> ExecResult:
        cwd = workdir or (self.repo_dir if os.path.isdir(self.repo_dir) else self.root)
        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "HOME": self.root, "GIT_TERMINAL_PROMPT": "0"},
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
            return ExecResult(124, "", timed_out=True)
        return ExecResult(proc.returncode or 0, out.decode("utf-8", errors="replace"))

    async def read_file(self, path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    async def write_file(self, path: str, content: bytes) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)

    async def destroy(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class LocalSandboxProvider(SandboxProvider):
    def __init__(self, base: str | None = None):
        self.base = os.path.abspath(base or get_settings().sandbox_local_root)
        os.makedirs(self.base, exist_ok=True)

    async def create(self, session_id: str) -> Sandbox:
        root = os.path.join(self.base, f"{session_id}-{uuid.uuid4().hex[:6]}")
        os.makedirs(root)
        return LocalSandbox(root)

    async def get(self, sandbox_id: str) -> Sandbox | None:
        root = sandbox_id.removeprefix("local:")
        return LocalSandbox(root) if os.path.isdir(root) else None
