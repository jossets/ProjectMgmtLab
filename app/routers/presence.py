from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import presence
from app.auth import Identity, get_identity
from app.db import get_db
from app.models import User

router = APIRouter()


class PingIn(BaseModel):
    page: str = Field(max_length=60)
    visible: bool


@router.post("/api/presence/ping")
def ping(payload: PingIn, identity: Identity = Depends(get_identity)):
    # anonymous visitors (open-link tools, no login) have nothing to
    # record — a no-op response keeps the client simple, no need for it to
    # know whether it's logged in before pinging
    if identity.user is not None:
        presence.record_ping(identity.user.id, payload.page, payload.visible)
    return {"ok": True}


@router.post("/api/presence/gone")
def gone(identity: Identity = Depends(get_identity)):
    if identity.user is not None:
        presence.record_gone(identity.user.id)
    return {"ok": True}


@router.get("/api/presence/students")
def list_students_presence(identity: Identity = Depends(get_identity), db: Session = Depends(get_db)):
    if not identity.is_admin:
        raise HTTPException(status_code=403, detail="Réservé à l'administrateur")
    students = db.query(User).filter(User.role == "student").order_by(User.username).all()
    result = []
    for s in students:
        entry = presence.get_presence(s.id)
        result.append(
            {
                "username": s.username,
                "online": entry is not None,
                "page": entry["page"] if entry else None,
                "visible": entry["visible"] if entry else False,
            }
        )
    return {"students": result}
