from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ExecResult:
    exit_code: int
    output: str
    timed_out: bool = False


class Sandbox(ABC):
    """Изолированное рабочее окружение одной сессии агента."""

    id: str
    # Абсолютный путь к клону репозитория внутри песочницы
    repo_dir: str

    @abstractmethod
    async def exec(self, command: str, timeout: int = 300, workdir: str | None = None) -> ExecResult: ...

    @abstractmethod
    async def read_file(self, path: str) -> bytes: ...

    @abstractmethod
    async def write_file(self, path: str, content: bytes) -> None: ...

    @abstractmethod
    async def destroy(self) -> None: ...


class SandboxProvider(ABC):
    @abstractmethod
    async def create(self, session_id: str) -> Sandbox: ...

    @abstractmethod
    async def get(self, sandbox_id: str) -> Sandbox | None:
        """Вернуть живую песочницу или None, если она уже удалена."""
