import os
import shutil
import sqlite3
import tempfile
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from starlette.background import BackgroundTask

from app.auth import Identity, get_identity
from app.db import DB_PATH, Base, engine, run_migrations

router = APIRouter()

# overridable for tests, same pattern as PROJECTMGR_DATA_DIR in app/db.py
MAX_BACKUP_BYTES = int(os.environ.get("PROJECTMGR_MAX_BACKUP_BYTES") or 200 * 1024 * 1024)
SQLITE_MAGIC = b"SQLite format 3\x00"


def require_admin(identity: Identity = Depends(get_identity)) -> Identity:
    # the true admin account only — not any teacher, even though
    # Identity.is_teacher also returns True for the admin sentinel
    if not identity.is_admin:
        raise HTTPException(status_code=403, detail="Réservé à l'administrateur")
    return identity


@router.get("/admin/backup")
def download_backup(identity: Identity = Depends(require_admin)):
    # SQLite's own online backup API rather than reading the file's bytes
    # directly — safe even if another connection is mid-write, unlike a
    # raw file copy which could grab a half-written page
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        source = sqlite3.connect(DB_PATH)
        try:
            dest = sqlite3.connect(tmp_path)
            try:
                source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()
    except Exception:
        os.remove(tmp_path)
        raise

    filename = f"projectmgr-backup-{datetime.utcnow():%Y%m%d-%H%M%S}.db"
    return FileResponse(
        tmp_path,
        media_type="application/octet-stream",
        filename=filename,
        background=BackgroundTask(os.remove, tmp_path),
    )


@router.post("/admin/restore")
async def restore_backup(
    file: UploadFile = File(...),
    identity: Identity = Depends(require_admin),
):
    content = await file.read(MAX_BACKUP_BYTES + 1)
    if len(content) > MAX_BACKUP_BYTES:
        raise HTTPException(status_code=413, detail="Fichier trop volumineux (200 Mo maximum)")
    if not content.startswith(SQLITE_MAGIC):
        raise HTTPException(status_code=415, detail="Ce fichier n'est pas une base SQLite valide")

    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)

        # validate integrity and that this looks like a ProjectMgr database
        # before touching the live file at all — a sufficiently mangled file
        # (valid header, garbage body) makes sqlite3 itself raise rather
        # than just returning a non-"ok" integrity_check row
        try:
            check_conn = sqlite3.connect(tmp_path)
            try:
                result = check_conn.execute("PRAGMA integrity_check").fetchone()
                if result is None or result[0] != "ok":
                    raise HTTPException(status_code=422, detail="Fichier corrompu (échec du contrôle d'intégrité SQLite)")
                tables = {row[0] for row in check_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "users" not in tables:
                    raise HTTPException(status_code=422, detail="Ce fichier ne ressemble pas à une sauvegarde ProjectMgr")
            finally:
                check_conn.close()
        except sqlite3.Error:
            raise HTTPException(status_code=422, detail="Fichier corrompu ou base SQLite invalide")

        # close every pooled connection before swapping the file out from
        # under them — SQLAlchemy reconnects lazily on the next query.
        # This app is documented single-worker/single-process (see
        # Readme.md); a request genuinely concurrent with this swap could
        # still hit a race, but that's an acceptable risk for a rare,
        # deliberate admin action rather than something worth a real lock.
        engine.dispose()

        if os.path.exists(DB_PATH):
            safety_copy = f"{DB_PATH}.before-restore-{datetime.utcnow():%Y%m%d-%H%M%S}"
            shutil.copy2(DB_PATH, safety_copy)
        shutil.copy2(tmp_path, DB_PATH)

        # a restored backup may predate tables/columns added by a newer
        # version of the app — add anything missing without touching
        # existing data
        Base.metadata.create_all(bind=engine)
        run_migrations(engine)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return RedirectResponse(url="/accounts", status_code=303)
