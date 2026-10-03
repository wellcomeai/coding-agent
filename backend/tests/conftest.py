import os
import subprocess

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault("SECRET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("SANDBOX_PROVIDER", "local")
os.environ.setdefault("TIMEWEB_API_TOKEN", "")
os.environ.setdefault("FRONTEND_DIST", "/nonexistent")
# Комментатор включается только в своих тестах, чтобы остальные не ходили в сеть
os.environ.setdefault("NARRATOR_MODEL", "")


@pytest.fixture
async def app_env(tmp_path, monkeypatch):
    """Чистая БД, локальные песочницы и «GitHub» в виде локального bare-репозитория."""
    from app import config, db, github_app
    from app.sandbox import set_provider
    from app.sandbox.local_sandbox import LocalSandboxProvider

    config.get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("GITHUB_WEB_URL", f"file://{tmp_path}/gh")
    config.get_settings.cache_clear()
    db.init_engine()
    await db.migrate()
    set_provider(LocalSandboxProvider(str(tmp_path / "sandboxes")))

    # bare-репозиторий owner/repo с веткой main
    bare = tmp_path / "gh" / "owner" / "repo.git"
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    (seed / "README.md").write_text("# Demo\n")
    git = ["git", "-C", str(seed), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(git + ["add", "."], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "init"], check=True)
    subprocess.run(git + ["push", "-q", str(bare), "main"], check=True)

    async def fake_installation_token(installation_id, repo):
        return "ghs_FAKE_TOKEN_123"

    monkeypatch.setattr(github_app, "installation_token", fake_installation_token)
    yield {"tmp": tmp_path, "bare": bare}
    await db.dispose()
    set_provider(None)
    config.get_settings.cache_clear()
