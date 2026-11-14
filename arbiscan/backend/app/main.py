"""FastAPI application entry point."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router, ws_router
from .config import Settings, get_settings
from .db import engine, init_engine
from .services.runtime import Runtime

log = logging.getLogger("arbiscan")


def run_migrations(database_url: str) -> None:
    """Apply Alembic migrations (creates the schema on first start)."""
    from alembic import command
    from alembic.config import Config

    here = Path(__file__).resolve().parent.parent
    cfg = Config(str(here / "alembic.ini"))
    cfg.set_main_option("script_location", str(here / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.attributes["app"] = True
    cfg.attributes["skip_logging"] = True
    command.upgrade(cfg, "head")


def create_app(settings: Settings | None = None, start_runtime: bool = True) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_engine(settings.database_url)
        run_migrations(settings.database_url)
        runtime = Runtime(settings)
        app.state.runtime = runtime
        if start_runtime and settings.collector_enabled:
            await runtime.start()
        yield
        await runtime.stop()
        engine().dispose()

    app = FastAPI(title="ArbiScan", version="1.0.0", lifespan=lifespan,
                  description="Prediction-market arbitrage research scanner (read-only; never places trades).")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Content-Type"], allow_credentials=False)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        return resp

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):  # pragma: no cover - safety net
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse({"detail": "internal error"}, status_code=500)

    app.include_router(router)
    app.include_router(ws_router)

    dist = Path(os.environ.get("ARBISCAN_FRONTEND_DIST", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            f = dist / path
            if path and f.is_file() and dist in f.resolve().parents:
                return FileResponse(f)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
