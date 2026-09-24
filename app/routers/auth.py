import time
from secrets import compare_digest

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import Identity, require_teacher_api, require_teacher_page
from app.config import ADMIN_PASSWORD, ADMIN_USERNAME
from app.db import get_db
from app.models import User
from app.security import hash_password, verify_password

router = APIRouter()
templates = Jinja2Templates(directory="templates")

MAX_LOGIN_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 5 * 60
_login_attempts: dict[str, list[float]] = {}

UserIdPath = Path(pattern=r"^[0-9a-f]{32}$")
USERNAME_PATTERN = r"^[A-Za-z0-9_.-]+$"


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _too_many_attempts(key: str) -> bool:
    now = time.monotonic()
    attempts = [t for t in _login_attempts.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
    _login_attempts[key] = attempts
    return len(attempts) >= MAX_LOGIN_ATTEMPTS


def _record_attempt(key: str) -> None:
    _login_attempts.setdefault(key, []).append(time.monotonic())


@router.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse(
        request, "login.html", {"error": None}
    )


def _safe_redirect_target(next: str) -> str:
    # only ever redirect to a same-origin path — "//evil.com" or "/\evil.com"
    # are protocol-relative tricks some browsers still honor as external
    if next.startswith("/") and not next.startswith(("//", "/\\")):
        return next
    return "/"


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(..., max_length=100),
    password: str = Form(..., max_length=200),
    next: str = Form(default="/", max_length=200),
    db: Session = Depends(get_db),
):
    key = _client_key(request)
    if _too_many_attempts(key):
        raise HTTPException(status_code=429, detail="Trop de tentatives, réessayez plus tard")

    redirect_url = _safe_redirect_target(next)

    if compare_digest(username, ADMIN_USERNAME) and compare_digest(password, ADMIN_PASSWORD):
        request.session.clear()
        request.session["admin"] = True
        return RedirectResponse(url=redirect_url, status_code=303)

    user = db.query(User).filter(User.username == username).first()
    if user is not None and not user.blocked and verify_password(password, user.password_hash):
        request.session.clear()
        request.session["user_id"] = user.id
        return RedirectResponse(url=redirect_url, status_code=303)

    _record_attempt(key)
    return templates.TemplateResponse(
        request, "login.html", {"error": "Identifiants invalides"}, status_code=401
    )


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


@router.get("/accounts")
def accounts_page(
    request: Request,
    identity: Identity = Depends(require_teacher_page),
    db: Session = Depends(get_db),
):
    teachers = db.query(User).filter(User.role == "teacher").order_by(User.username).all()
    students = db.query(User).filter(User.role == "student").order_by(User.username).all()
    return templates.TemplateResponse(
        request, "accounts.html", {"identity": identity, "teachers": teachers, "students": students}
    )


@router.post("/accounts/teachers")
def create_account(
    username: str = Form(..., min_length=3, max_length=50, pattern=USERNAME_PATTERN),
    password: str = Form(..., min_length=8, max_length=200),
    role: str = Form(default="teacher", pattern=r"^(teacher|student)$"),
    identity: Identity = Depends(require_teacher_api),
    db: Session = Depends(get_db),
):
    if db.query(User).filter(User.username == username).first() is not None:
        raise HTTPException(status_code=409, detail="Ce nom d'utilisateur existe déjà")
    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.commit()
    return RedirectResponse(url="/accounts", status_code=303)


@router.post("/accounts/{user_id}/block")
def block_account(
    user_id: str = UserIdPath,
    identity: Identity = Depends(require_teacher_api),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Compte introuvable")
    if user.id == identity.id:
        raise HTTPException(status_code=400, detail="Vous ne pouvez pas bloquer votre propre compte")
    user.blocked = True
    db.commit()
    return RedirectResponse(url="/accounts", status_code=303)


@router.post("/accounts/{user_id}/unblock")
def unblock_account(
    user_id: str = UserIdPath,
    identity: Identity = Depends(require_teacher_api),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Compte introuvable")
    user.blocked = False
    db.commit()
    return RedirectResponse(url="/accounts", status_code=303)
