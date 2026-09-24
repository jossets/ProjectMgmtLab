import json
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from app.activity_log import get_history, log_activity
from app.auth import Identity, get_identity, get_ws_identity
from app.db import SessionLocal, get_db
from app.models import KanbanBoard, KanbanCard, KanbanColumn
from app.session_access import check_board_access, check_session_ownership_for_attach, enforce_board_access_api, enforce_board_access_page

router = APIRouter()
templates = Jinja2Templates(directory="templates")

KanbanIdPath = Path(pattern=r"^[0-9a-f]{32}$")
KANBAN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

MAX_COLUMNS = 20
MAX_CARDS_PER_COLUMN = 200
DEFAULT_COLUMN_TITLES = ["À faire", "En cours", "Terminé"]
DEFAULT_CARD_COLOR = "#fff3bf"


def check_hex_color(v: str) -> str:
    if not HEX_COLOR_RE.match(v):
        raise ValueError("couleur invalide (format attendu : #rrggbb)")
    return v


def _truncate(text: str, max_len: int = 60) -> str:
    text = text.strip()
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


class RenameIn(BaseModel):
    name: str = Field(max_length=200)


class ColumnCreateMsg(BaseModel):
    op: str = Field(pattern="^create_column$")
    title: str = Field(default="Nouvelle colonne", max_length=100)
    client_ref: str | None = Field(default=None, max_length=64)


class ColumnRenameMsg(BaseModel):
    op: str = Field(pattern="^rename_column$")
    id: int = Field(ge=1)
    title: str = Field(max_length=100)


class ColumnDeleteMsg(BaseModel):
    op: str = Field(pattern="^delete_column$")
    id: int = Field(ge=1)


class ColumnMoveMsg(BaseModel):
    op: str = Field(pattern="^move_column$")
    id: int = Field(ge=1)
    index: int = Field(ge=0, le=10_000)


class CardCreateMsg(BaseModel):
    op: str = Field(pattern="^create_card$")
    column_id: int = Field(ge=1)
    text: str = Field(default="", max_length=500)
    color: str = DEFAULT_CARD_COLOR
    client_ref: str | None = Field(default=None, max_length=64)

    _check_color = field_validator("color")(check_hex_color)


class CardUpdateMsg(BaseModel):
    op: str = Field(pattern="^update_card$")
    id: int = Field(ge=1)
    text: str = Field(default="", max_length=500)
    color: str = DEFAULT_CARD_COLOR

    _check_color = field_validator("color")(check_hex_color)


class CardMoveMsg(BaseModel):
    op: str = Field(pattern="^move_card$")
    id: int = Field(ge=1)
    column_id: int = Field(ge=1)
    index: int = Field(ge=0, le=10_000)


class CardDeleteMsg(BaseModel):
    op: str = Field(pattern="^delete_card$")
    id: int = Field(ge=1)


def get_kanban_or_404(kanban_id: str, db: Session) -> KanbanBoard:
    board = db.get(KanbanBoard, kanban_id)
    if board is None:
        raise HTTPException(status_code=404, detail="Kanban introuvable")
    return board


def serialize_card(card: KanbanCard) -> dict:
    return {
        "id": card.id,
        "column_id": card.column_id,
        "order_index": card.order_index,
        "text": card.text,
        "color": card.color,
    }


def serialize_column(column: KanbanColumn) -> dict:
    return {
        "id": column.id,
        "title": column.title,
        "order_index": column.order_index,
        "cards": [serialize_card(c) for c in column.cards],
    }


@router.post("/kanban/new")
def new_kanban(
    session_id: str | None = Form(default=None),
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    if session_id is not None:
        check_session_ownership_for_attach(identity, session_id, db)
    board = KanbanBoard(name="Nouveau Kanban", session_id=session_id)
    db.add(board)
    db.flush()
    for i, title in enumerate(DEFAULT_COLUMN_TITLES):
        db.add(KanbanColumn(board_id=board.id, title=title, order_index=i))
    db.commit()
    return RedirectResponse(url=f"/kanban/{board.id}", status_code=303)


@router.get("/kanban/{kanban_id}")
def kanban_page(
    request: Request,
    kanban_id: str = KanbanIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    board = get_kanban_or_404(kanban_id, db)
    enforce_board_access_page(identity, board.session_id, db)
    return templates.TemplateResponse(request, "kanban.html", {"board": board, "identity": identity})


@router.get("/api/kanban/{kanban_id}")
def kanban_json(
    kanban_id: str = KanbanIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    board = get_kanban_or_404(kanban_id, db)
    enforce_board_access_api(identity, board.session_id, db)
    return {
        "id": board.id,
        "name": board.name,
        "columns": [serialize_column(c) for c in board.columns],
    }


@router.patch("/api/kanban/{kanban_id}")
def rename_kanban(
    payload: RenameIn,
    kanban_id: str = KanbanIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    board = get_kanban_or_404(kanban_id, db)
    enforce_board_access_api(identity, board.session_id, db)
    new_name = payload.name.strip() or board.name
    if new_name != board.name:
        log_activity(db, "kanban", kanban_id, identity, "renamed", f'Kanban renommé « {board.name} » → « {new_name} »')
        board.name = new_name
    db.commit()
    return {"ok": True, "name": board.name}


@router.get("/api/kanban/{kanban_id}/history")
def kanban_history(
    kanban_id: str = KanbanIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    board = get_kanban_or_404(kanban_id, db)
    enforce_board_access_api(identity, board.session_id, db)
    return {"entries": get_history(db, "kanban", kanban_id)}


class ConnectionManager:
    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = {}

    async def connect(self, kanban_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._rooms.setdefault(kanban_id, set()).add(websocket)

    def disconnect(self, kanban_id: str, websocket: WebSocket) -> None:
        room = self._rooms.get(kanban_id)
        if room is not None:
            room.discard(websocket)
            if not room:
                self._rooms.pop(kanban_id, None)

    async def broadcast(self, kanban_id: str, message: dict) -> None:
        dead = []
        for ws in self._rooms.get(kanban_id, set()):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(kanban_id, ws)


manager = ConnectionManager()


def get_card_or_none(card_id: int, kanban_id: str, db: Session) -> tuple[KanbanCard | None, KanbanColumn | None]:
    card = db.get(KanbanCard, card_id)
    if card is None:
        return None, None
    column = db.get(KanbanColumn, card.column_id)
    if column is None or column.board_id != kanban_id:
        return None, None
    return card, column


async def handle_message(kanban_id: str, raw: str, db: Session, websocket: WebSocket, identity: Identity) -> None:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        await websocket.send_json({"op": "error", "detail": "JSON invalide"})
        return

    op = payload.get("op") if isinstance(payload, dict) else None

    try:
        if op == "create_column":
            msg = ColumnCreateMsg.model_validate(payload)
            count = db.query(KanbanColumn).filter(KanbanColumn.board_id == kanban_id).count()
            if count >= MAX_COLUMNS:
                await websocket.send_json({"op": "error", "detail": f"Trop de colonnes (max {MAX_COLUMNS})"})
                return
            column = KanbanColumn(board_id=kanban_id, title=msg.title, order_index=count)
            db.add(column)
            log_activity(db, "kanban", kanban_id, identity, "created", f'Colonne « {msg.title} » créée')
            db.commit()
            db.refresh(column)
            await manager.broadcast(
                kanban_id,
                {"op": "column_created", "column": serialize_column(column), "client_ref": msg.client_ref},
            )

        elif op == "rename_column":
            msg = ColumnRenameMsg.model_validate(payload)
            column = db.get(KanbanColumn, msg.id)
            if column is None or column.board_id != kanban_id:
                await websocket.send_json({"op": "error", "detail": "Colonne introuvable"})
                return
            if msg.title != column.title:
                log_activity(
                    db, "kanban", kanban_id, identity, "updated", f'Colonne renommée « {column.title} » → « {msg.title} »'
                )
                column.title = msg.title
            db.commit()
            await manager.broadcast(kanban_id, {"op": "column_renamed", "id": column.id, "title": column.title})

        elif op == "delete_column":
            msg = ColumnDeleteMsg.model_validate(payload)
            column = db.get(KanbanColumn, msg.id)
            if column is None or column.board_id != kanban_id:
                await websocket.send_json({"op": "error", "detail": "Colonne introuvable"})
                return
            title = column.title
            db.delete(column)
            log_activity(db, "kanban", kanban_id, identity, "deleted", f'Colonne « {title} » supprimée')
            db.commit()
            await manager.broadcast(kanban_id, {"op": "column_deleted", "id": msg.id})

        elif op == "move_column":
            msg = ColumnMoveMsg.model_validate(payload)
            column = db.get(KanbanColumn, msg.id)
            if column is None or column.board_id != kanban_id:
                await websocket.send_json({"op": "error", "detail": "Colonne introuvable"})
                return
            siblings = (
                db.query(KanbanColumn)
                .filter(KanbanColumn.board_id == kanban_id, KanbanColumn.id != column.id)
                .order_by(KanbanColumn.order_index)
                .all()
            )
            index = max(0, min(msg.index, len(siblings)))
            siblings.insert(index, column)
            for i, c in enumerate(siblings):
                c.order_index = i
            db.commit()
            await manager.broadcast(kanban_id, {"op": "columns_reordered", "order": [c.id for c in siblings]})

        elif op == "create_card":
            msg = CardCreateMsg.model_validate(payload)
            column = db.get(KanbanColumn, msg.column_id)
            if column is None or column.board_id != kanban_id:
                await websocket.send_json({"op": "error", "detail": "Colonne introuvable"})
                return
            count = db.query(KanbanCard).filter(KanbanCard.column_id == column.id).count()
            if count >= MAX_CARDS_PER_COLUMN:
                await websocket.send_json(
                    {"op": "error", "detail": f"Trop de cartes dans cette colonne (max {MAX_CARDS_PER_COLUMN})"}
                )
                return
            card = KanbanCard(column_id=column.id, order_index=count, text=msg.text, color=msg.color)
            db.add(card)
            suffix = f' : « {_truncate(msg.text)} »' if msg.text.strip() else ""
            log_activity(db, "kanban", kanban_id, identity, "created", f'Carte ajoutée dans « {column.title} »{suffix}')
            db.commit()
            db.refresh(card)
            await manager.broadcast(
                kanban_id, {"op": "card_created", "card": serialize_card(card), "client_ref": msg.client_ref}
            )

        elif op == "update_card":
            msg = CardUpdateMsg.model_validate(payload)
            card, column = get_card_or_none(msg.id, kanban_id, db)
            if card is None:
                await websocket.send_json({"op": "error", "detail": "Carte introuvable"})
                return
            if msg.text != card.text or msg.color != card.color:
                suffix = f' : « {_truncate(msg.text)} »' if msg.text.strip() else ""
                log_activity(db, "kanban", kanban_id, identity, "updated", f'Carte modifiée dans « {column.title} »{suffix}')
                card.text = msg.text
                card.color = msg.color
            db.commit()
            await manager.broadcast(kanban_id, {"op": "card_updated", "card": serialize_card(card)})

        elif op == "move_card":
            msg = CardMoveMsg.model_validate(payload)
            card, source_column = get_card_or_none(msg.id, kanban_id, db)
            if card is None:
                await websocket.send_json({"op": "error", "detail": "Carte introuvable"})
                return
            dest_column = db.get(KanbanColumn, msg.column_id)
            if dest_column is None or dest_column.board_id != kanban_id:
                await websocket.send_json({"op": "error", "detail": "Colonne introuvable"})
                return

            source_column_id = source_column.id
            source_title = source_column.title
            dest_title = dest_column.title
            dest_siblings = (
                db.query(KanbanCard)
                .filter(KanbanCard.column_id == dest_column.id, KanbanCard.id != card.id)
                .order_by(KanbanCard.order_index)
                .all()
            )
            if source_column_id != dest_column.id and len(dest_siblings) >= MAX_CARDS_PER_COLUMN:
                await websocket.send_json(
                    {"op": "error", "detail": f"Trop de cartes dans cette colonne (max {MAX_CARDS_PER_COLUMN})"}
                )
                return

            index = max(0, min(msg.index, len(dest_siblings)))
            dest_siblings.insert(index, card)
            card.column_id = dest_column.id
            for i, c in enumerate(dest_siblings):
                c.order_index = i
            db.flush()

            affected = {dest_column.id: [c.id for c in dest_siblings]}
            if source_column_id != dest_column.id:
                source_siblings = (
                    db.query(KanbanCard)
                    .filter(KanbanCard.column_id == source_column_id)
                    .order_by(KanbanCard.order_index)
                    .all()
                )
                for i, c in enumerate(source_siblings):
                    c.order_index = i
                affected[source_column_id] = [c.id for c in source_siblings]
                log_activity(
                    db, "kanban", kanban_id, identity, "updated", f'Carte déplacée de « {source_title} » vers « {dest_title} »'
                )

            db.commit()
            await manager.broadcast(kanban_id, {"op": "card_moved", "columns": affected})

        elif op == "delete_card":
            msg = CardDeleteMsg.model_validate(payload)
            card, column = get_card_or_none(msg.id, kanban_id, db)
            if card is None:
                await websocket.send_json({"op": "error", "detail": "Carte introuvable"})
                return
            column_title = column.title
            card_text = card.text
            column_id = card.column_id
            db.delete(card)
            db.flush()
            siblings = (
                db.query(KanbanCard)
                .filter(KanbanCard.column_id == column_id)
                .order_by(KanbanCard.order_index)
                .all()
            )
            for i, c in enumerate(siblings):
                c.order_index = i
            suffix = f' : « {_truncate(card_text)} »' if card_text.strip() else ""
            log_activity(db, "kanban", kanban_id, identity, "deleted", f'Carte supprimée de « {column_title} »{suffix}')
            db.commit()
            await manager.broadcast(kanban_id, {"op": "card_deleted", "id": msg.id, "column_id": column_id})

        else:
            await websocket.send_json({"op": "error", "detail": "Opération inconnue"})

    except ValidationError:
        await websocket.send_json({"op": "error", "detail": "Payload invalide"})


@router.websocket("/ws/kanban/{kanban_id}")
async def kanban_ws(websocket: WebSocket, kanban_id: str):
    if not KANBAN_ID_RE.match(kanban_id):
        await websocket.close(code=1008)
        return

    # short-lived: just for the initial access check — see the matching
    # comment in whiteboard.py's whiteboard_ws for why this connection must
    # not hold one DB connection checked out for its entire (potentially
    # hours-long) lifetime instead of per message.
    db = SessionLocal()
    try:
        board = db.get(KanbanBoard, kanban_id)
        if board is None:
            await websocket.close(code=1008)
            return

        identity = get_ws_identity(websocket, db)
        if not check_board_access(identity, board.session_id, db):
            await websocket.close(code=1008)
            return
    finally:
        db.close()

    await manager.connect(kanban_id, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            db = SessionLocal()
            try:
                await handle_message(kanban_id, raw, db, websocket, identity)
            finally:
                db.close()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(kanban_id, websocket)
