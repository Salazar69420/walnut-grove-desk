from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security.auth import require
from app.services import importers as imp

router = APIRouter(prefix="/api/admin/import", tags=["admin"])


@router.post("/{kind}")
def import_file(kind: str, file: UploadFile = File(...), actor: User = Depends(require("admin.import")), db: Session = Depends(get_db)):
    if kind not in imp.KINDS:
        raise HTTPException(status_code=404, detail="Unknown import type")
    data = file.file.read(imp.MAX_BYTES + 1)
    try:
        raw = imp.parse_file(file.filename or "", data, kind)
    except imp.ImportError_ as e:
        raise HTTPException(status_code=400 if "larger" not in str(e) else 413, detail=str(e))
    good, bad = imp.validate(kind, raw)
    if good:
        imp.load(db, kind, good, actor)
        db.commit()
    return {"kind": kind, "accepted": len(good), "rejected": bad[:50], "rejected_total": len(bad)}
