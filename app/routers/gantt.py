from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.activity_log import get_history, log_activity
from app.auth import Identity, get_identity
from app.db import get_db
from app.models import Gantt, GanttTask
from app.session_access import check_session_ownership_for_attach, enforce_board_access_api, enforce_board_access_page

router = APIRouter()
templates = Jinja2Templates(directory="templates")

MAX_TASKS_PER_GANTT = 1000
GanttIdPath = Path(pattern=r"^[0-9a-f]{32}$")


class TaskIn(BaseModel):
    # id: set by the client for a task that already exists server-side (it
    # echoes back what GET /api/gantt/{id} returned), left None for a task
    # created client-side since the last save. client_ref lets the client
    # match that just-created task back up with the id the server assigns,
    # the same client_ref/server-id handshake already used by the
    # whiteboard/kanban websocket "create" ops.
    id: int | None = Field(default=None, ge=1)
    client_ref: str | None = Field(default=None, max_length=64)
    order_index: int = Field(ge=0, le=100_000)
    indent_level: int = Field(default=0, ge=0, le=20)
    name: str = Field(default="", max_length=500)
    start_date: date | None = None
    end_date: date | None = None
    progress_pct: int = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def check_date_order(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date doit être postérieure ou égale à start_date")
        return self


class RenameIn(BaseModel):
    name: str = Field(max_length=200)


def get_gantt_or_404(gantt_id: str, db: Session) -> Gantt:
    gantt = db.get(Gantt, gantt_id)
    if gantt is None:
        raise HTTPException(status_code=404, detail="Gantt introuvable")
    return gantt


def _describe_task_changes(task: GanttTask, new: TaskIn) -> str | None:
    parts = []
    if task.name != new.name:
        parts.append(f'nom « {task.name or "—"} » → « {new.name or "—"} »')
    if task.start_date != new.start_date or task.end_date != new.end_date:
        old_range = f'{task.start_date.isoformat() if task.start_date else "?"} → {task.end_date.isoformat() if task.end_date else "?"}'
        new_range = f'{new.start_date.isoformat() if new.start_date else "?"} → {new.end_date.isoformat() if new.end_date else "?"}'
        parts.append(f"dates {old_range} → {new_range}")
    if task.progress_pct != new.progress_pct:
        parts.append(f"progression {task.progress_pct}% → {new.progress_pct}%")
    if task.indent_level != new.indent_level:
        parts.append(f"indentation niveau {task.indent_level} → {new.indent_level}")
    return ", ".join(parts) if parts else None


@router.post("/gantt/new")
def new_gantt(
    session_id: str | None = Form(default=None),
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    if session_id is not None:
        check_session_ownership_for_attach(identity, session_id, db)
    gantt = Gantt(name="Nouveau Gantt", session_id=session_id)
    db.add(gantt)
    db.commit()
    return RedirectResponse(url=f"/gantt/{gantt.id}", status_code=303)


@router.get("/gantt/{gantt_id}")
def gantt_page(
    request: Request,
    gantt_id: str = GanttIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    gantt = get_gantt_or_404(gantt_id, db)
    enforce_board_access_page(identity, gantt.session_id, db)
    return templates.TemplateResponse(request, "gantt.html", {"gantt": gantt, "identity": identity})


@router.get("/api/gantt/{gantt_id}")
def gantt_json(
    gantt_id: str = GanttIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    gantt = get_gantt_or_404(gantt_id, db)
    enforce_board_access_api(identity, gantt.session_id, db)
    return {
        "id": gantt.id,
        "name": gantt.name,
        "tasks": [
            {
                "id": t.id,
                "order_index": t.order_index,
                "indent_level": t.indent_level,
                "name": t.name,
                "start_date": t.start_date.isoformat() if t.start_date else None,
                "end_date": t.end_date.isoformat() if t.end_date else None,
                "progress_pct": t.progress_pct,
            }
            for t in gantt.tasks
        ],
    }


@router.put("/api/gantt/{gantt_id}/tasks")
def replace_tasks(
    tasks: list[TaskIn],
    gantt_id: str = GanttIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    if len(tasks) > MAX_TASKS_PER_GANTT:
        raise HTTPException(status_code=422, detail=f"Trop de tâches (max {MAX_TASKS_PER_GANTT})")
    gantt = get_gantt_or_404(gantt_id, db)
    enforce_board_access_api(identity, gantt.session_id, db)

    # ids only ever match tasks belonging to *this* gantt — an id copied
    # from another board simply won't be found here and falls through to
    # the "create" branch, so it can never be used to edit someone else's task
    existing_by_id = {t.id: t for t in gantt.tasks}
    seen_ids: set[int] = set()
    created_refs: list[tuple[GanttTask, str]] = []

    for t in tasks:
        existing = existing_by_id.get(t.id) if t.id is not None else None
        if existing is not None:
            seen_ids.add(existing.id)
            changes = _describe_task_changes(existing, t)
            existing.order_index = t.order_index
            existing.indent_level = t.indent_level
            existing.name = t.name
            existing.start_date = t.start_date
            existing.end_date = t.end_date
            existing.progress_pct = t.progress_pct
            if changes:
                log_activity(db, "gantt", gantt_id, identity, "updated", f'Tâche « {t.name or "sans nom"} » : {changes}')
        else:
            new_task = GanttTask(
                gantt_id=gantt_id,
                order_index=t.order_index,
                indent_level=t.indent_level,
                name=t.name,
                start_date=t.start_date,
                end_date=t.end_date,
                progress_pct=t.progress_pct,
            )
            db.add(new_task)
            log_activity(db, "gantt", gantt_id, identity, "created", f'Tâche « {t.name or "sans nom"} » créée')
            if t.client_ref:
                created_refs.append((new_task, t.client_ref))

    for old_id, old_task in existing_by_id.items():
        if old_id not in seen_ids:
            log_activity(db, "gantt", gantt_id, identity, "deleted", f'Tâche « {old_task.name or "sans nom"} » supprimée')
            db.delete(old_task)

    db.flush()
    created = [{"client_ref": ref, "id": task.id} for task, ref in created_refs]
    db.commit()
    return {"ok": True, "created": created}


@router.patch("/api/gantt/{gantt_id}")
def rename_gantt(
    payload: RenameIn,
    gantt_id: str = GanttIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    gantt = get_gantt_or_404(gantt_id, db)
    enforce_board_access_api(identity, gantt.session_id, db)
    new_name = payload.name.strip() or gantt.name
    if new_name != gantt.name:
        log_activity(db, "gantt", gantt_id, identity, "renamed", f'Gantt renommé « {gantt.name} » → « {new_name} »')
        gantt.name = new_name
    db.commit()
    return {"ok": True, "name": gantt.name}


@router.get("/api/gantt/{gantt_id}/history")
def gantt_history(
    gantt_id: str = GanttIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    gantt = get_gantt_or_404(gantt_id, db)
    enforce_board_access_api(identity, gantt.session_id, db)
    return {"entries": get_history(db, "gantt", gantt_id)}
