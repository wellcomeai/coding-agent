import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .agent.runner import runner
from .billing import price_book
from .config import get_settings
from .db import create_all, init_engine
from .routers import account, auth, payments, repos, sessions, setup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


async def _reaper() -> None:
    while True:
        await asyncio.sleep(120)
        try:
            n = await runner.reap_idle()
            if n:
                log.info("Освобождено простаивающих песочниц: %d", n)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка очистки песочниц")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_engine()
    await create_all()
    await setup.apply_db_config()
    await runner.recover_after_restart()
    asyncio.create_task(price_book.refresh())
    reaper = asyncio.create_task(_reaper())
    yield
    reaper.cancel()
    with suppress(asyncio.CancelledError):
        await reaper


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title=s.app_name, lifespan=lifespan)
    for r in (auth.router, repos.router, sessions.router, account.router, setup.router, payments.router):
        app.include_router(r)

    @app.get("/api/health")
    async def health():
        return {"ok": True, "version": get_settings().app_version}

    dist = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", s.frontend_dist))
    if os.path.isdir(dist):
        assets = os.path.join(dist, "assets")
        if os.path.isdir(assets):
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            file = os.path.join(dist, path)
            if path and os.path.isfile(file) and os.path.abspath(file).startswith(dist):
                return FileResponse(file)
            return FileResponse(os.path.join(dist, "index.html"))

    return app


app = create_app()
