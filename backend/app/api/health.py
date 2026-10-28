from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import db as app_db

router = APIRouter()


@router.get("/healthz", include_in_schema=False)
def healthz():
    try:
        with app_db.get_sessionmaker()() as db:
            db.execute(text("SELECT 1"))
            db.rollback()
    except Exception:
        return JSONResponse({"status": "error"}, status_code=503)
    return {"status": "ok"}
