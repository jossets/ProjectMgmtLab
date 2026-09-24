import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth import Identity
from app.models import CourseSession, SessionMembership

SESSION_ID_RE = re.compile(r"^[A-Z0-9]{7}$")


def check_board_access(identity: Identity, session_id: str | None, db: Session) -> bool:
    if session_id is None:
        return True  # unchanged: a tool with no session stays open to anyone with the link
    if identity.is_admin:
        return True
    session = db.get(CourseSession, session_id)
    if session is None:
        return False
    # the owner can always manage their own session's tools, blocked or not
    # — "blocked" is meant to cut off students/members, not lock the
    # teacher out of their own material
    if identity.user is not None and session.teacher_id == identity.user.id:
        return True
    if not session.active:
        return False
    if identity.user is None:
        return False
    return (
        db.query(SessionMembership).filter_by(session_id=session_id, user_id=identity.user.id).first()
        is not None
    )


def enforce_board_access_page(identity: Identity, session_id: str | None, db: Session) -> None:
    if check_board_access(identity, session_id, db):
        return
    if not identity.is_authenticated:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    raise HTTPException(status_code=403, detail="Accès réservé aux membres de cette session")


def enforce_board_access_api(identity: Identity, session_id: str | None, db: Session) -> None:
    if not check_board_access(identity, session_id, db):
        raise HTTPException(status_code=403, detail="Accès réservé aux membres de cette session")


def check_session_ownership_for_attach(identity: Identity, session_id: str, db: Session) -> CourseSession:
    # validates format + existence + ownership when attaching a *new* tool
    # to a session at creation time (POST /gantt/new?session_id=... etc.)
    if not SESSION_ID_RE.match(session_id):
        raise HTTPException(status_code=422, detail="Identifiant de session invalide")
    session = db.get(CourseSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session introuvable")
    if not (identity.is_admin or (identity.user is not None and identity.user.id == session.teacher_id)):
        raise HTTPException(status_code=403, detail="Réservé au propriétaire de la session")
    return session
