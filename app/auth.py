from dataclasses import dataclass

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette.websockets import WebSocket

from app.db import get_db
from app.models import User


@dataclass
class Identity:
    is_admin: bool
    user: User | None

    @property
    def is_teacher(self) -> bool:
        return self.is_admin or (self.user is not None and self.user.role == "teacher")

    @property
    def is_authenticated(self) -> bool:
        return self.is_admin or self.user is not None

    @property
    def id(self) -> str | None:
        if self.is_admin:
            return "admin"
        return self.user.id if self.user else None


def identity_from_session_data(session_data: dict, db: Session) -> Identity:
    if session_data.get("admin"):
        return Identity(is_admin=True, user=None)
    user_id = session_data.get("user_id")
    if user_id:
        user = db.get(User, user_id)
        if user is not None and not user.blocked:
            return Identity(is_admin=False, user=user)
    return Identity(is_admin=False, user=None)


def get_identity(request: Request, db: Session = Depends(get_db)) -> Identity:
    return identity_from_session_data(request.session, db)


def get_ws_identity(websocket: WebSocket, db: Session) -> Identity:
    return identity_from_session_data(websocket.session, db)


def require_login_page(identity: Identity = Depends(get_identity)) -> Identity:
    if not identity.is_authenticated:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return identity


def require_teacher_page(identity: Identity = Depends(get_identity)) -> Identity:
    if not identity.is_teacher:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return identity


def require_login_api(identity: Identity = Depends(get_identity)) -> Identity:
    if not identity.is_authenticated:
        raise HTTPException(status_code=401, detail="Connexion requise")
    return identity


def require_teacher_api(identity: Identity = Depends(get_identity)) -> Identity:
    if not identity.is_teacher:
        raise HTTPException(status_code=403, detail="Réservé aux enseignants")
    return identity
