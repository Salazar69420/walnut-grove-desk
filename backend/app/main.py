"""Walnut Grove Desk, FastAPI application."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from app import config, db, migrate
from app.api import admin, admin_ai, admin_telus, afterhours, auth, brief, callbacks, drafts, export, feed, health, imports, overnight, results, shifts, sim
from app.assistant import routes as assistant_routes
from app.retell import routes as retell_routes
from app.security.auth import csrf_guard
from app.security.headers import SecurityHeadersMiddleware
from app.services.broadcaster import broadcaster

log = logging.getLogger("walnut")

NO_DOCS_NAMES = {"docs", "redoc", "openapi.json"}


def create_app(settings: config.Settings | None = None) -> FastAPI:
    if settings is not None:
        config.set_settings(settings)
    s = config.get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        db.init_engine(s.db_path)
        if not s.skip_migrate:
            await asyncio.to_thread(migrate.upgrade_head, s.db_path)
        broadcaster.bind_loop(asyncio.get_running_loop())
        log.info("walnut-desk started (env=%s)", s.env)
        yield
        broadcaster.reset()

    prod = s.is_production
    app = FastAPI(
        title="Walnut Grove Desk",
        lifespan=lifespan,
        docs_url=None if prod else "/docs",
        redoc_url=None if prod else "/redoc",
        openapi_url=None if prod else "/openapi.json",
        dependencies=[Depends(csrf_guard)],
    )
    app.add_middleware(SecurityHeadersMiddleware)

    for r in (auth.router, admin.router, callbacks.router, results.router, drafts.router, shifts.router, feed.router, export.router, health.router,
              admin_ai.router, admin_telus.router, imports.router, overnight.router, sim.router, afterhours.router, brief.router,
              assistant_routes.router, retell_routes.router):
        app.include_router(r)

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def api_not_found(rest: str):
        return JSONResponse({"detail": "Not found"}, status_code=404)

    dist = Path(s.frontend_dist)

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if prod and path.strip("/").split("/")[0] in NO_DOCS_NAMES:
            raise HTTPException(status_code=404, detail="Not found")
        index = dist / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        if path:
            candidate = (dist / path).resolve()
            if candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            if "." in Path(path).name:
                raise HTTPException(status_code=404, detail="Not found")  # missing asset, not a route
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
