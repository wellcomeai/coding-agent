"""Песочница = отдельный Docker-контейнер на сессию (ограничения CPU/RAM/PIDs, non-root)."""

import asyncio
import io
import posixpath
import shlex
import tarfile
import time

import docker
from docker.errors import NotFound

from ..config import get_settings
from .base import ExecResult, Sandbox, SandboxProvider

LABEL = "coding-agent.session"
WORKSPACE = "/workspace"
SANDBOX_UID = 1000


class DockerSandbox(Sandbox):
    def __init__(self, container):
        self._c = container
        self.id = container.id
        self.repo_dir = f"{WORKSPACE}/repo"

    async def exec(self, command: str, timeout: int = 300, workdir: str | None = None) -> ExecResult:
        # coreutils timeout убивает процесс по истечении времени; -k добивает через 5 сек.
        wrapped = f"timeout -k 5 {int(timeout)} bash -lc {shlex.quote(command)}"

        def run():
            return self._c.exec_run(
                ["bash", "-c", wrapped], workdir=workdir or self.repo_dir, user=str(SANDBOX_UID), demux=False
            )

        res = await asyncio.to_thread(run)
        out = (res.output or b"").decode("utf-8", errors="replace")
        return ExecResult(res.exit_code, out, timed_out=res.exit_code == 124)

    async def read_file(self, path: str) -> bytes:
        def run():
            bits, _ = self._c.get_archive(path)
            buf = io.BytesIO(b"".join(bits))
            with tarfile.open(fileobj=buf) as tar:
                member = tar.getmembers()[0]
                if not member.isfile():
                    raise IsADirectoryError(path)
                f = tar.extractfile(member)
                return f.read() if f else b""

        try:
            return await asyncio.to_thread(run)
        except NotFound as e:
            raise FileNotFoundError(path) from e

    async def write_file(self, path: str, content: bytes) -> None:
        directory, name = posixpath.split(path)
        await self.exec(f"mkdir -p {shlex.quote(directory)}", timeout=30, workdir="/")

        def run():
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                info = tarfile.TarInfo(name)
                info.size = len(content)
                info.mtime = int(time.time())
                info.mode = 0o644
                info.uid = info.gid = SANDBOX_UID
                tar.addfile(info, io.BytesIO(content))
            buf.seek(0)
            self._c.put_archive(directory, buf.getvalue())

        await asyncio.to_thread(run)

    async def destroy(self) -> None:
        def run():
            try:
                self._c.remove(force=True)
            except NotFound:
                pass

        await asyncio.to_thread(run)


class DockerSandboxProvider(SandboxProvider):
    def __init__(self) -> None:
        self._client = docker.from_env()

    async def create(self, session_id: str) -> Sandbox:
        s = get_settings()

        def run():
            # Остановленный контейнер прошлой песочницы этой сессии (например, после перезагрузки хоста)
            try:
                self._client.containers.get(f"agent-{session_id}").remove(force=True)
            except NotFound:
                pass
            return self._client.containers.run(
                s.sandbox_image,
                command=["sleep", "infinity"],
                detach=True,
                labels={LABEL: session_id},
                name=f"agent-{session_id}",
                mem_limit=s.sandbox_memory,
                nano_cpus=int(s.sandbox_cpus * 1e9),
                pids_limit=s.sandbox_pids_limit,
                network=s.sandbox_network,
                working_dir=WORKSPACE,
                user=str(SANDBOX_UID),
                security_opt=["no-new-privileges"],
                cap_drop=["ALL"],
            )

        container = await asyncio.to_thread(run)
        return DockerSandbox(container)

    async def get(self, sandbox_id: str) -> Sandbox | None:
        def run():
            try:
                c = self._client.containers.get(sandbox_id)
            except NotFound:
                return None
            return c if c.status == "running" else None

        c = await asyncio.to_thread(run)
        return DockerSandbox(c) if c else None
