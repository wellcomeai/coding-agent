from ..config import get_settings
from .base import ExecResult, Sandbox, SandboxProvider

_provider: SandboxProvider | None = None


def get_provider() -> SandboxProvider:
    global _provider
    if _provider is None:
        kind = get_settings().sandbox_provider
        if kind == "docker":
            from .docker_sandbox import DockerSandboxProvider

            _provider = DockerSandboxProvider()
        elif kind == "local":
            from .local_sandbox import LocalSandboxProvider

            _provider = LocalSandboxProvider()
        else:
            raise RuntimeError(f"Неизвестный SANDBOX_PROVIDER: {kind}")
    return _provider


def set_provider(provider: SandboxProvider | None) -> None:
    global _provider
    _provider = provider


__all__ = ["ExecResult", "Sandbox", "SandboxProvider", "get_provider", "set_provider"]
