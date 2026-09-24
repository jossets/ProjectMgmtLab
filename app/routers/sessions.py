import re
import time

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import Identity, get_identity, require_login_page, require_teacher_api, require_teacher_page
from app.db import get_db
from app.models import CourseSession, Gantt, KanbanBoard, QcmSession, SessionMembership, User, Whiteboard, generate_join_code
from app.routers.auth import USERNAME_PATTERN
from app.security import hash_password

router = APIRouter()
templates = Jinja2Templates(directory="templates")

SessionIdPath = Path(pattern=r"^[A-Z0-9]{7}$")
SESSION_ID_RE = re.compile(r"^[A-Z0-9]{7}$")
UserIdPath = Path(pattern=r"^[0-9a-f]{32}$")
ToolIdPath = Path(pattern=r"^[0-9a-f]{32}$")

TOOL_MODELS = {"gantt": Gantt, "whiteboard": Whiteboard, "kanban": KanbanBoard}
TOOL_TYPE_PATTERN = r"^(gantt|whiteboard|kanban)$"

MAX_JOIN_CODE_ATTEMPTS = 5

MAX_SIGNUP_ATTEMPTS = 10
SIGNUP_WINDOW_SECONDS = 5 * 60
_signup_attempts: dict[str, list[float]] = {}


def _signup_client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _too_many_signups(key: str) -> bool:
    now = time.monotonic()
    attempts = [t for t in _signup_attempts.get(key, []) if now - t < SIGNUP_WINDOW_SECONDS]
    _signup_attempts[key] = attempts
    return len(attempts) >= MAX_SIGNUP_ATTEMPTS


def _record_signup(key: str) -> None:
    _signup_attempts.setdefault(key, []).append(time.monotonic())


def _generate_unique_session_id(db: Session) -> str:
    for _ in range(MAX_JOIN_CODE_ATTEMPTS):
        code = generate_join_code()
        if db.get(CourseSession, code) is None:
            return code
    raise HTTPException(status_code=500, detail="Impossible de générer un code de session, réessayez")


def get_session_or_404(session_id: str, db: Session) -> CourseSession:
    session = db.get(CourseSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session introuvable")
    return session


def _is_owner(identity: Identity, session: CourseSession) -> bool:
    return identity.is_admin or (identity.user is not None and identity.user.id == session.teacher_id)


def _require_owner(identity: Identity, session: CourseSession) -> None:
    if not _is_owner(identity, session):
        raise HTTPException(status_code=403, detail="Réservé au propriétaire de la session")


@router.get("/sessions")
def list_sessions(
    request: Request,
    identity: Identity = Depends(require_teacher_page),
    db: Session = Depends(get_db),
):
    query = db.query(CourseSession).order_by(CourseSession.created_at.desc())
    if not identity.is_admin:
        query = query.filter(CourseSession.teacher_id == identity.user.id)
    return templates.TemplateResponse(
        request, "sessions_list.html", {"identity": identity, "sessions": query.all()}
    )


@router.post("/sessions/new")
def new_session(identity: Identity = Depends(require_teacher_api), db: Session = Depends(get_db)):
    session = CourseSession(
        id=_generate_unique_session_id(db),
        teacher_id=identity.user.id if identity.user else None,
    )
    db.add(session)
    db.flush()
    board = Whiteboard(name=f"Tableau blanc — {session.name}", session_id=session.id)
    db.add(board)
    db.commit()
    return RedirectResponse(url=f"/sessions/{session.id}", status_code=303)


@router.get("/sessions/{id}")
def session_detail(
    request: Request,
    id: str = SessionIdPath,
    identity: Identity = Depends(require_login_page),
    db: Session = Depends(get_db),
):
    session = get_session_or_404(id, db)
    is_owner = _is_owner(identity, session)

    is_member = False
    if not is_owner and identity.user is not None:
        is_member = (
            db.query(SessionMembership).filter_by(session_id=session.id, user_id=identity.user.id).first()
            is not None
        )

    if not is_owner and not is_member:
        if identity.is_teacher:
            raise HTTPException(status_code=403, detail="Réservé au propriétaire de la session")
        # a student (or anyone else) who isn't a member yet: send them
        # through the join flow instead of a bare access-denied page
        raise HTTPException(status_code=303, headers={"Location": f"/join/{session.id}"})

    teacher = db.get(User, session.teacher_id) if session.teacher_id else None
    gantts = db.query(Gantt).filter(Gantt.session_id == session.id).order_by(Gantt.created_at).all()
    whiteboards = db.query(Whiteboard).filter(Whiteboard.session_id == session.id).order_by(Whiteboard.created_at).all()
    kanbans = db.query(KanbanBoard).filter(KanbanBoard.session_id == session.id).order_by(KanbanBoard.created_at).all()
    members = []
    available_students = []
    qcm_sessions = []
    if is_owner:
        members = [m.user for m in session.memberships]
        member_ids = {m.id for m in members}
        available_students = [
            s
            for s in db.query(User).filter(User.role == "student").order_by(User.username).all()
            if s.id not in member_ids
        ]
        qcm_sessions = (
            db.query(QcmSession).filter(QcmSession.session_id == session.id).order_by(QcmSession.created_at.desc()).all()
        )

    return templates.TemplateResponse(
        request,
        "session.html",
        {
            "identity": identity,
            "session": session,
            "is_owner": is_owner,
            "teacher_username": teacher.username if teacher else "admin",
            "gantts": gantts,
            "whiteboards": whiteboards,
            "kanbans": kanbans,
            "members": members,
            "available_students": available_students,
            "qcm_sessions": qcm_sessions,
        },
    )


@router.post("/sessions/{id}/rename")
def rename_session(
    name: str = Form(..., max_length=200),
    id: str = SessionIdPath,
    identity: Identity = Depends(require_teacher_api),
    db: Session = Depends(get_db),
):
    session = get_session_or_404(id, db)
    _require_owner(identity, session)
    session.name = name.strip() or session.name
    db.commit()
    return RedirectResponse(url=f"/sessions/{session.id}", status_code=303)


@router.post("/sessions/{id}/toggle-active")
def toggle_active(
    id: str = SessionIdPath,
    identity: Identity = Depends(require_teacher_api),
    db: Session = Depends(get_db),
):
    session = get_session_or_404(id, db)
    _require_owner(identity, session)
    session.active = not session.active
    db.commit()
    return RedirectResponse(url=f"/sessions/{session.id}", status_code=303)


@router.post("/sessions/{id}/toggle-signups")
def toggle_signups(
    id: str = SessionIdPath,
    identity: Identity = Depends(require_teacher_api),
    db: Session = Depends(get_db),
):
    session = get_session_or_404(id, db)
    _require_owner(identity, session)
    session.allow_new_accounts = not session.allow_new_accounts
    db.commit()
    return RedirectResponse(url=f"/sessions/{session.id}", status_code=303)


@router.post("/sessions/{id}/members")
def add_member(
    user_id: str = Form(..., pattern=r"^[0-9a-f]{32}$"),
    id: str = SessionIdPath,
    identity: Identity = Depends(require_teacher_api),
    db: Session = Depends(get_db),
):
    session = get_session_or_404(id, db)
    _require_owner(identity, session)
    student = db.get(User, user_id)
    if student is None or student.role != "student":
        raise HTTPException(status_code=404, detail="Élève introuvable")
    existing = db.query(SessionMembership).filter_by(session_id=session.id, user_id=student.id).first()
    if existing is None:
        db.add(SessionMembership(session_id=session.id, user_id=student.id))
        db.commit()
    return RedirectResponse(url=f"/sessions/{session.id}", status_code=303)


@router.post("/sessions/{id}/members/{user_id}/remove")
def remove_member(
    id: str = SessionIdPath,
    user_id: str = UserIdPath,
    identity: Identity = Depends(require_teacher_api),
    db: Session = Depends(get_db),
):
    session = get_session_or_404(id, db)
    _require_owner(identity, session)
    db.query(SessionMembership).filter_by(session_id=session.id, user_id=user_id).delete()
    db.commit()
    return RedirectResponse(url=f"/sessions/{session.id}", status_code=303)


@router.get("/join/{id}")
def join_page(
    request: Request,
    id: str = SessionIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    session = get_session_or_404(id, db)

    if identity.is_teacher:
        return RedirectResponse(url=f"/sessions/{session.id}", status_code=303)

    return templates.TemplateResponse(request, "join.html", {"identity": identity, "session": session})


@router.post("/join/{id}")
def join_submit(
    id: str = SessionIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    session = get_session_or_404(id, db)
    if identity.user is None or identity.user.role != "student":
        raise HTTPException(status_code=403, detail="Connectez-vous avec un compte élève pour rejoindre")
    existing = db.query(SessionMembership).filter_by(session_id=session.id, user_id=identity.user.id).first()
    if existing is None:
        db.add(SessionMembership(session_id=session.id, user_id=identity.user.id))
        db.commit()
    return RedirectResponse(url=f"/sessions/{session.id}", status_code=303)


@router.post("/join/{id}/create-account")
def join_create_account(
    request: Request,
    username: str = Form(..., min_length=3, max_length=50, pattern=USERNAME_PATTERN),
    password: str = Form(..., min_length=8, max_length=200),
    id: str = SessionIdPath,
    db: Session = Depends(get_db),
):
    key = _signup_client_key(request)
    if _too_many_signups(key):
        raise HTTPException(status_code=429, detail="Trop de tentatives, réessayez plus tard")
    _record_signup(key)

    session = get_session_or_404(id, db)
    if not session.allow_new_accounts:
        raise HTTPException(status_code=403, detail="La création de compte est désactivée pour cette session")
    if db.query(User).filter(User.username == username).first() is not None:
        raise HTTPException(status_code=409, detail="Ce nom d'utilisateur existe déjà")

    user = User(username=username, password_hash=hash_password(password), role="student")
    db.add(user)
    db.flush()
    db.add(SessionMembership(session_id=session.id, user_id=user.id))
    db.commit()

    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse(url=f"/sessions/{session.id}", status_code=303)


@router.get("/profile")
def profile_page(
    request: Request,
    identity: Identity = Depends(require_login_page),
    db: Session = Depends(get_db),
):
    sessions = []
    if identity.user is not None and identity.user.role == "student":
        memberships = db.query(SessionMembership).filter(SessionMembership.user_id == identity.user.id).all()
        sessions = [m.session for m in memberships]
    return templates.TemplateResponse(request, "profile.html", {"identity": identity, "sessions": sessions})


@router.post("/profile/username")
def update_username(
    username: str = Form(..., min_length=3, max_length=50, pattern=USERNAME_PATTERN),
    identity: Identity = Depends(require_login_page),
    db: Session = Depends(get_db),
):
    if identity.user is None:
        raise HTTPException(status_code=403, detail="Réservé aux comptes enseignant et élève")
    existing = db.query(User).filter(User.username == username, User.id != identity.user.id).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Ce nom d'utilisateur existe déjà")
    identity.user.username = username
    db.commit()
    return RedirectResponse(url="/profile", status_code=303)


@router.post("/sessions/{id}/attach")
def attach_tool(
    tool_type: str = Form(..., pattern=TOOL_TYPE_PATTERN),
    tool_id: str = Form(..., pattern=r"^[0-9a-f]{32}$"),
    id: str = SessionIdPath,
    identity: Identity = Depends(require_teacher_api),
    db: Session = Depends(get_db),
):
    session = get_session_or_404(id, db)
    _require_owner(identity, session)
    tool = db.get(TOOL_MODELS[tool_type], tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail="Outil introuvable")
    # a tool with no session is fair game (same "open to anyone with the
    # link" rule as check_board_access), but one already attached to
    # someone else's session must not be re-attachable here — without this,
    # any teacher could steal another teacher's board into their own
    # session just by knowing its id, silently cutting the original session
    # off from it in the process
    if tool.session_id is not None and tool.session_id != session.id:
        current_session = db.get(CourseSession, tool.session_id)
        if current_session is not None and not _is_owner(identity, current_session):
            raise HTTPException(status_code=403, detail="Cet outil appartient déjà à une autre session")
    tool.session_id = session.id
    db.commit()
    return RedirectResponse(url=f"/sessions/{session.id}", status_code=303)


@router.post("/sessions/{id}/detach")
def detach_tool(
    tool_type: str = Form(..., pattern=TOOL_TYPE_PATTERN),
    tool_id: str = Form(..., pattern=r"^[0-9a-f]{32}$"),
    id: str = SessionIdPath,
    identity: Identity = Depends(require_teacher_api),
    db: Session = Depends(get_db),
):
    session = get_session_or_404(id, db)
    _require_owner(identity, session)
    tool = db.get(TOOL_MODELS[tool_type], tool_id)
    if tool is None or tool.session_id != session.id:
        raise HTTPException(status_code=404, detail="Outil introuvable")
    tool.session_id = None
    db.commit()
    return RedirectResponse(url=f"/sessions/{session.id}", status_code=303)
