import json
import os
import re
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy.orm import Session

from app.activity_log import actor_label, get_history, log_activity
from app.auth import Identity, get_identity, get_ws_identity
from app.db import DATA_DIR, SessionLocal, get_db
from app.models import ActivityLog, Whiteboard, WhiteboardElement
from app.session_access import (
    check_board_access,
    check_session_ownership_for_attach,
    enforce_board_access_api,
    enforce_board_access_page,
)
from app.url_fetch import PublicFetchError, fetch_public_url

router = APIRouter()
templates = Jinja2Templates(directory="templates")

WhiteboardIdPath = Path(pattern=r"^[0-9a-f]{32}$")
WHITEBOARD_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# overridable for tests, same pattern as PROJECTMGR_MAX_BACKUP_BYTES in app/routers/admin.py
MAX_ELEMENTS_PER_WHITEBOARD = int(os.environ.get("PROJECTMGR_MAX_WHITEBOARD_ELEMENTS") or 2000)
ALLOWED_TYPES = {"text", "line", "image", "table"}
ELEMENT_TYPE_LABELS = {"text": "texte", "line": "trait", "image": "image", "table": "tableau"}
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

ALLOWED_REACTIONS = {"heart", "thumbsup", "thumbsdown"}
MAX_VOTERS_PER_REACTION = 1_000
VOTER_ID_RE = re.compile(r"^[0-9a-f]{32}$")

UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
MAX_IMAGE_BYTES = 5 * 1024 * 1024
UPLOAD_FILENAME_PATTERN = r"^[0-9a-f]{32}\.(png|jpg|gif|webp)$"
IMAGE_MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


def sniff_image_ext(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    return None


class RenameIn(BaseModel):
    name: str = Field(max_length=200)


class ImageUrlIn(BaseModel):
    url: str = Field(max_length=2000)


class TextRun(BaseModel):
    text: str = Field(default="", max_length=2000)
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    font_size: int = Field(default=16, ge=8, le=96)


class TextParagraph(BaseModel):
    bullet: bool = False
    runs: list[TextRun] = Field(default_factory=list)

    @field_validator("runs")
    @classmethod
    def check_runs(cls, v: list[TextRun]) -> list[TextRun]:
        if len(v) > 100:
            raise ValueError("trop de segments dans ce paragraphe (max 100)")
        return v


class TextData(BaseModel):
    paragraphs: list[TextParagraph] = Field(default_factory=lambda: [TextParagraph(runs=[TextRun()])])
    color: str = "#1f2430"
    bg_color: str = "#ffffff"
    border_color: str = "#adb5bd"
    reactions: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("color", "bg_color", "border_color")
    @classmethod
    def check_hex_color(cls, v: str) -> str:
        if not HEX_COLOR_RE.match(v):
            raise ValueError("couleur invalide (format attendu : #rrggbb)")
        return v

    @field_validator("paragraphs")
    @classmethod
    def check_paragraphs(cls, v: list[TextParagraph]) -> list[TextParagraph]:
        if not v:
            raise ValueError("le texte doit avoir au moins un paragraphe")
        if len(v) > 200:
            raise ValueError("trop de paragraphes (max 200)")
        return v

    @field_validator("reactions")
    @classmethod
    def check_reactions(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        if not set(v.keys()) <= ALLOWED_REACTIONS:
            raise ValueError(f"réaction invalide (autorisées : {sorted(ALLOWED_REACTIONS)})")
        for voters in v.values():
            if len(voters) > MAX_VOTERS_PER_REACTION:
                raise ValueError(f"trop de votes pour cette réaction (max {MAX_VOTERS_PER_REACTION})")
            for voter_id in voters:
                if not VOTER_ID_RE.match(voter_id):
                    raise ValueError("identifiant de voteur invalide")
        return v


class LineData(BaseModel):
    points: list[tuple[float, float]] = Field(default_factory=list)
    stroke_color: str = "#1f2430"
    stroke_width: float = Field(default=3, ge=1, le=40)
    arrow_start: bool = False
    arrow_end: bool = False

    @field_validator("points")
    @classmethod
    def check_points(cls, v: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if len(v) > 2000:
            raise ValueError("trop de points pour ce trait")
        return v

    @field_validator("stroke_color")
    @classmethod
    def check_hex_color(cls, v: str) -> str:
        if not HEX_COLOR_RE.match(v):
            raise ValueError("couleur invalide (format attendu : #rrggbb)")
        return v


class ImageData(BaseModel):
    src: str = Field(max_length=500)

    @field_validator("src")
    @classmethod
    def check_src(cls, v: str) -> str:
        if not v.startswith("/uploads/whiteboard/"):
            raise ValueError("src invalide : doit provenir de l'upload du tableau blanc")
        return v


class TableCell(BaseModel):
    text: str = Field(default="", max_length=500)
    bold: bool = False
    italic: bool = False


class TableData(BaseModel):
    rows: list[list[TableCell]] = Field(default_factory=lambda: [[TableCell(), TableCell()] for _ in range(5)])
    font_size: int = Field(default=14, ge=8, le=48)

    @field_validator("rows")
    @classmethod
    def check_rows(cls, v: list[list[TableCell]]) -> list[list[TableCell]]:
        if not v:
            raise ValueError("le tableau doit avoir au moins une ligne")
        if len(v) > 50:
            raise ValueError("trop de lignes (max 50)")
        ncols = len(v[0])
        if ncols == 0 or ncols > 20:
            raise ValueError("nombre de colonnes invalide (1 à 20)")
        for row in v:
            if len(row) != ncols:
                raise ValueError("toutes les lignes doivent avoir le même nombre de colonnes")
        return v


class ElementIn(BaseModel):
    type: str
    x: float = Field(ge=-100_000, le=100_000)
    y: float = Field(ge=-100_000, le=100_000)
    width: float = Field(ge=0, le=100_000)
    height: float = Field(ge=0, le=100_000)
    z_index: int = Field(default=0, ge=-1_000_000, le=1_000_000)
    data: dict = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def check_type(cls, v: str) -> str:
        if v not in ALLOWED_TYPES:
            raise ValueError(f"type doit être l'un de {sorted(ALLOWED_TYPES)}")
        return v

    @model_validator(mode="after")
    def normalize_data(self):
        # every value check_type() accepts is handled below — no fallback
        # branch, so a new type can't silently skip data validation
        if self.type == "text":
            self.data = TextData(**self.data).model_dump()
        elif self.type == "line":
            self.data = LineData(**self.data).model_dump()
        elif self.type == "image":
            self.data = ImageData(**self.data).model_dump()
        elif self.type == "table":
            self.data = TableData(**self.data).model_dump()
        return self


class WsCreateMsg(BaseModel):
    op: str = Field(pattern="^create$")
    element: ElementIn
    client_ref: str | None = Field(default=None, max_length=64)


class WsUpdateMsg(BaseModel):
    op: str = Field(pattern="^update$")
    id: int = Field(ge=1)
    element: ElementIn


class WsDeleteMsg(BaseModel):
    op: str = Field(pattern="^delete$")
    id: int = Field(ge=1)


class WsToggleLockMsg(BaseModel):
    op: str = Field(pattern="^toggle_lock$")
    id: int = Field(ge=1)


class WsRestoreMsg(BaseModel):
    op: str = Field(pattern="^restore$")
    log_id: int = Field(ge=1)


class WsCursorMsg(BaseModel):
    op: str = Field(pattern="^cursor$")
    x: float = Field(ge=-1_000_000, le=1_000_000)
    y: float = Field(ge=-1_000_000, le=1_000_000)


class WsEditingMsg(BaseModel):
    op: str = Field(pattern="^editing$")
    id: int = Field(ge=1)
    editing: bool
    cell: tuple[int, int] | None = Field(default=None)

    @field_validator("cell")
    @classmethod
    def check_cell(cls, v: tuple[int, int] | None) -> tuple[int, int] | None:
        if v is not None and (v[0] < 0 or v[1] < 0 or v[0] > 1000 or v[1] > 1000):
            raise ValueError("cellule invalide")
        return v


class WsReactMsg(BaseModel):
    op: str = Field(pattern="^react$")
    id: int = Field(ge=1)
    reaction: str
    voter_id: str

    @field_validator("reaction")
    @classmethod
    def check_reaction(cls, v: str) -> str:
        if v not in ALLOWED_REACTIONS:
            raise ValueError(f"réaction invalide (autorisées : {sorted(ALLOWED_REACTIONS)})")
        return v

    @field_validator("voter_id")
    @classmethod
    def check_voter_id(cls, v: str) -> str:
        if not VOTER_ID_RE.match(v):
            raise ValueError("identifiant de voteur invalide")
        return v


def get_whiteboard_or_404(whiteboard_id: str, db: Session) -> Whiteboard:
    board = db.get(Whiteboard, whiteboard_id)
    if board is None:
        raise HTTPException(status_code=404, detail="Tableau blanc introuvable")
    return board


def _content_preview(element_type: str, data: dict, max_len: int = 80) -> str:
    """Short plain-text preview of an element's content, for the activity log."""
    if element_type == "text":
        parts = [run.get("text", "") for para in data.get("paragraphs", []) for run in para.get("runs", [])]
        text = " ".join(p for p in parts if p).strip()
    elif element_type == "table":
        parts = [cell.get("text", "") for row in data.get("rows", []) for cell in row]
        text = ", ".join(p for p in parts if p).strip()
    else:
        return ""
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def serialize_element(element: WhiteboardElement) -> dict:
    return {
        "id": element.id,
        "type": element.type,
        "x": element.x,
        "y": element.y,
        "width": element.width,
        "height": element.height,
        "z_index": element.z_index,
        "data": element.data,
        "locked": element.locked,
    }


@router.post("/whiteboard/new")
def new_whiteboard(
    session_id: str | None = Form(default=None),
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    if session_id is not None:
        check_session_ownership_for_attach(identity, session_id, db)
    board = Whiteboard(name="Nouveau tableau blanc", session_id=session_id)
    db.add(board)
    db.commit()
    return RedirectResponse(url=f"/whiteboard/{board.id}", status_code=303)


@router.get("/whiteboard/{whiteboard_id}")
def whiteboard_page(
    request: Request,
    whiteboard_id: str = WhiteboardIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    board = get_whiteboard_or_404(whiteboard_id, db)
    enforce_board_access_page(identity, board.session_id, db)
    return templates.TemplateResponse(request, "whiteboard.html", {"board": board, "identity": identity})


@router.get("/api/whiteboard/{whiteboard_id}")
def whiteboard_json(
    whiteboard_id: str = WhiteboardIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    board = get_whiteboard_or_404(whiteboard_id, db)
    enforce_board_access_api(identity, board.session_id, db)
    return {
        "id": board.id,
        "name": board.name,
        "elements": [serialize_element(e) for e in board.elements],
    }


@router.patch("/api/whiteboard/{whiteboard_id}")
def rename_whiteboard(
    payload: RenameIn,
    whiteboard_id: str = WhiteboardIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    board = get_whiteboard_or_404(whiteboard_id, db)
    enforce_board_access_api(identity, board.session_id, db)
    new_name = payload.name.strip() or board.name
    if new_name != board.name:
        log_activity(db, "whiteboard", whiteboard_id, identity, "renamed", f'Tableau renommé « {board.name} » → « {new_name} »')
        board.name = new_name
    db.commit()
    return {"ok": True, "name": board.name}


@router.get("/api/whiteboard/{whiteboard_id}/history")
def whiteboard_history(
    whiteboard_id: str = WhiteboardIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    board = get_whiteboard_or_404(whiteboard_id, db)
    enforce_board_access_api(identity, board.session_id, db)
    return {"entries": get_history(db, "whiteboard", whiteboard_id)}


def _store_whiteboard_image(whiteboard_id: str, content: bytes, ext: str) -> str:
    board_dir = os.path.join(UPLOAD_DIR, whiteboard_id)
    os.makedirs(board_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(board_dir, filename), "wb") as f:
        f.write(content)
    return f"/uploads/whiteboard/{whiteboard_id}/{filename}"


@router.post("/api/whiteboard/{whiteboard_id}/upload-image")
async def upload_image(
    whiteboard_id: str = WhiteboardIdPath,
    file: UploadFile = File(...),
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    board = get_whiteboard_or_404(whiteboard_id, db)
    enforce_board_access_api(identity, board.session_id, db)

    content = await file.read(MAX_IMAGE_BYTES + 1)
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image trop volumineuse (5 Mo maximum)")
    if not content:
        raise HTTPException(status_code=400, detail="Fichier vide")

    ext = sniff_image_ext(content)
    if ext is None:
        raise HTTPException(status_code=415, detail="Type d'image non supporté (png, jpeg, webp, gif uniquement)")

    return {"url": _store_whiteboard_image(whiteboard_id, content, ext)}


@router.post("/api/whiteboard/{whiteboard_id}/upload-image-url")
def upload_image_from_url(
    payload: ImageUrlIn,
    whiteboard_id: str = WhiteboardIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    # covers pasting an image copied from another site's page: that often
    # puts HTML (with an <img src="..."> pointing back at that site) on the
    # clipboard instead of raw image bytes, so the client asks us to fetch
    # it — see app/url_fetch.py for the SSRF guarding this relies on
    board = get_whiteboard_or_404(whiteboard_id, db)
    enforce_board_access_api(identity, board.session_id, db)

    try:
        content = fetch_public_url(payload.url, MAX_IMAGE_BYTES)
    except PublicFetchError as err:
        raise HTTPException(status_code=422, detail=str(err))

    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image trop volumineuse (5 Mo maximum)")
    if not content:
        raise HTTPException(status_code=400, detail="Fichier vide")

    ext = sniff_image_ext(content)
    if ext is None:
        raise HTTPException(status_code=415, detail="Type d'image non supporté (png, jpeg, webp, gif uniquement)")

    return {"url": _store_whiteboard_image(whiteboard_id, content, ext)}


@router.get("/uploads/whiteboard/{whiteboard_id}/{filename}")
def get_uploaded_image(
    whiteboard_id: str = WhiteboardIdPath,
    filename: str = Path(pattern=UPLOAD_FILENAME_PATTERN),
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    board = get_whiteboard_or_404(whiteboard_id, db)
    enforce_board_access_api(identity, board.session_id, db)
    path = os.path.join(UPLOAD_DIR, whiteboard_id, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Image introuvable")
    ext = filename.rsplit(".", 1)[-1]
    return FileResponse(path, media_type=IMAGE_MEDIA_TYPES[ext])


class ConnectionManager:
    def __init__(self) -> None:
        # value: a random per-connection id, stable for that connection's
        # whole lifetime — lets every viewer's cursor be tracked/removed
        # individually, including several tabs of the same logged-in user
        self._rooms: dict[str, dict[WebSocket, str]] = {}
        # tracks what each connection currently has an "editing" badge open
        # on, so it can be cleared for everyone else if that connection
        # drops without sending an explicit editing:false first
        self._editing: dict[str, dict[WebSocket, dict]] = {}

    async def connect(self, whiteboard_id: str, websocket: WebSocket) -> str:
        await websocket.accept()
        connection_id = uuid.uuid4().hex
        self._rooms.setdefault(whiteboard_id, {})[websocket] = connection_id
        return connection_id

    def disconnect(self, whiteboard_id: str, websocket: WebSocket) -> str | None:
        room = self._rooms.get(whiteboard_id)
        if room is None:
            return None
        connection_id = room.pop(websocket, None)
        if not room:
            self._rooms.pop(whiteboard_id, None)
        return connection_id

    def set_editing(self, whiteboard_id: str, websocket: WebSocket, info: dict) -> None:
        self._editing.setdefault(whiteboard_id, {})[websocket] = info

    def pop_editing(self, whiteboard_id: str, websocket: WebSocket) -> dict | None:
        room = self._editing.get(whiteboard_id)
        if room is None:
            return None
        info = room.pop(websocket, None)
        if not room:
            self._editing.pop(whiteboard_id, None)
        return info

    async def broadcast(self, whiteboard_id: str, message: dict, exclude: WebSocket | None = None) -> None:
        dead = []
        for ws in self._rooms.get(whiteboard_id, {}):
            if ws is exclude:
                continue
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(whiteboard_id, ws)


manager = ConnectionManager()


async def handle_message(
    whiteboard_id: str, raw: str, db: Session, websocket: WebSocket, identity: Identity, connection_id: str
) -> None:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        await websocket.send_json({"op": "error", "detail": "JSON invalide"})
        return

    op = payload.get("op") if isinstance(payload, dict) else None

    try:
        if op == "create":
            msg = WsCreateMsg.model_validate(payload)
            count = db.query(WhiteboardElement).filter(WhiteboardElement.whiteboard_id == whiteboard_id).count()
            if count >= MAX_ELEMENTS_PER_WHITEBOARD:
                await websocket.send_json(
                    {"op": "error", "detail": f"Trop d'éléments sur ce tableau (max {MAX_ELEMENTS_PER_WHITEBOARD})"}
                )
                return
            element = WhiteboardElement(whiteboard_id=whiteboard_id, **msg.element.model_dump())
            db.add(element)
            log_activity(
                db, "whiteboard", whiteboard_id, identity, "created", f"Élément {ELEMENT_TYPE_LABELS[msg.element.type]} ajouté"
            )
            db.commit()
            db.refresh(element)
            await manager.broadcast(
                whiteboard_id,
                {"op": "created", "element": serialize_element(element), "client_ref": msg.client_ref},
            )

        elif op == "update":
            msg = WsUpdateMsg.model_validate(payload)
            element = db.get(WhiteboardElement, msg.id)
            if element is None or element.whiteboard_id != whiteboard_id:
                await websocket.send_json({"op": "error", "detail": "Élément introuvable"})
                return
            if msg.element.type != element.type:
                # an update payload's data is only validated against the type
                # it declares — silently accepting a different type here
                # would let any "update" op retype an element (e.g. text ->
                # line) and re-shape its data out from under its own schema
                await websocket.send_json({"op": "error", "detail": "Impossible de changer le type d'un élément existant"})
                return
            for field, value in msg.element.model_dump().items():
                setattr(element, field, value)
            log_activity(
                db, "whiteboard", whiteboard_id, identity, "updated", f"Élément {ELEMENT_TYPE_LABELS[element.type]} modifié"
            )
            db.commit()
            await manager.broadcast(whiteboard_id, {"op": "updated", "element": serialize_element(element)})

        elif op == "delete":
            msg = WsDeleteMsg.model_validate(payload)
            element = db.get(WhiteboardElement, msg.id)
            if element is None or element.whiteboard_id != whiteboard_id:
                await websocket.send_json({"op": "error", "detail": "Élément introuvable"})
                return
            element_type = element.type
            snapshot = serialize_element(element)  # kept in the log entry so this can be undone
            preview = _content_preview(element_type, element.data)
            summary = f"Élément {ELEMENT_TYPE_LABELS[element_type]} supprimé"
            if preview:
                summary += f' : « {preview} »'
            db.delete(element)
            log_activity(db, "whiteboard", whiteboard_id, identity, "deleted", summary, payload=snapshot)
            db.commit()
            await manager.broadcast(whiteboard_id, {"op": "deleted", "id": msg.id})

        elif op == "toggle_lock":
            msg = WsToggleLockMsg.model_validate(payload)
            element = db.get(WhiteboardElement, msg.id)
            if element is None or element.whiteboard_id != whiteboard_id:
                await websocket.send_json({"op": "error", "detail": "Élément introuvable"})
                return
            element.locked = not element.locked
            log_activity(
                db,
                "whiteboard",
                whiteboard_id,
                identity,
                "updated",
                f"Élément {ELEMENT_TYPE_LABELS[element.type]} {'fixé' if element.locked else 'détaché'}",
            )
            db.commit()
            await manager.broadcast(whiteboard_id, {"op": "updated", "element": serialize_element(element)})

        elif op == "restore":
            msg = WsRestoreMsg.model_validate(payload)
            entry = db.get(ActivityLog, msg.log_id)
            if (
                entry is None
                or entry.tool_type != "whiteboard"
                or entry.tool_id != whiteboard_id
                or entry.action != "deleted"
                or not entry.payload
            ):
                await websocket.send_json({"op": "error", "detail": "Rien à restaurer pour cette entrée"})
                return
            count = db.query(WhiteboardElement).filter(WhiteboardElement.whiteboard_id == whiteboard_id).count()
            if count >= MAX_ELEMENTS_PER_WHITEBOARD:
                await websocket.send_json(
                    {"op": "error", "detail": f"Trop d'éléments sur ce tableau (max {MAX_ELEMENTS_PER_WHITEBOARD})"}
                )
                return
            snapshot = entry.payload
            try:
                restored = ElementIn.model_validate(
                    {k: snapshot.get(k) for k in ("type", "x", "y", "width", "height", "z_index", "data")}
                )
            except ValidationError:
                await websocket.send_json({"op": "error", "detail": "Impossible de restaurer cet élément"})
                return
            element = WhiteboardElement(whiteboard_id=whiteboard_id, locked=bool(snapshot.get("locked")), **restored.model_dump())
            db.add(element)
            log_activity(
                db,
                "whiteboard",
                whiteboard_id,
                identity,
                "created",
                f"Élément {ELEMENT_TYPE_LABELS[restored.type]} restauré depuis l'historique",
            )
            db.commit()
            db.refresh(element)
            await manager.broadcast(whiteboard_id, {"op": "created", "element": serialize_element(element), "client_ref": None})

        elif op == "react":
            msg = WsReactMsg.model_validate(payload)
            element = db.get(WhiteboardElement, msg.id)
            if element is None or element.whiteboard_id != whiteboard_id or element.type != "text":
                await websocket.send_json({"op": "error", "detail": "Élément introuvable"})
                return
            # toggle server-side (read-modify-write within this single
            # commit) rather than accepting a client-computed list, so
            # concurrent votes from different students can't clobber each
            # other the way a full-state "update" would. one vote per
            # voter_id per reaction — voter_id is an anonymous id the
            # client generates and persists locally (no accounts yet).
            # older elements stored reactions as plain counts (int) before
            # per-voter tracking existed — self-heal by treating anything
            # that isn't already a list as no votes yet, same pattern as
            # normalizeReactions() on the client
            raw_reactions = element.data.get("reactions") or {}
            reactions = {k: (list(v) if isinstance(v, list) else []) for k, v in raw_reactions.items()}
            voters = reactions.get(msg.reaction, [])
            if msg.voter_id in voters:
                voters = [v for v in voters if v != msg.voter_id]
            elif len(voters) >= MAX_VOTERS_PER_REACTION:
                await websocket.send_json({"op": "error", "detail": "Trop de votes pour cette réaction"})
                return
            else:
                voters = voters + [msg.voter_id]
            reactions[msg.reaction] = voters
            element.data = {**element.data, "reactions": reactions}
            db.commit()
            db.refresh(element)
            await manager.broadcast(whiteboard_id, {"op": "updated", "element": serialize_element(element)})

        elif op == "cursor":
            # ephemeral — never persisted, relayed to everyone else on this
            # board so each viewer sees where everyone else's pointer is
            msg = WsCursorMsg.model_validate(payload)
            await manager.broadcast(
                whiteboard_id,
                {"op": "cursor", "id": connection_id, "label": actor_label(identity), "x": msg.x, "y": msg.y},
                exclude=websocket,
            )

        elif op == "editing":
            # ephemeral — never persisted, just relayed so every other
            # viewer can show "so-and-so is editing" in the corner of that
            # text block / table cell. Tracked per-connection so it can be
            # cleared automatically if this connection drops mid-edit.
            msg = WsEditingMsg.model_validate(payload)
            element = db.get(WhiteboardElement, msg.id)
            if element is None or element.whiteboard_id != whiteboard_id:
                await websocket.send_json({"op": "error", "detail": "Élément introuvable"})
                return
            if msg.editing:
                manager.set_editing(whiteboard_id, websocket, {"id": msg.id, "cell": msg.cell})
            else:
                manager.pop_editing(whiteboard_id, websocket)
            await manager.broadcast(
                whiteboard_id,
                {
                    "op": "editing",
                    "id": msg.id,
                    "editing": msg.editing,
                    "cell": list(msg.cell) if msg.cell else None,
                    "editor_id": connection_id,
                    "label": actor_label(identity),
                },
                exclude=websocket,
            )

        else:
            await websocket.send_json({"op": "error", "detail": "Opération inconnue"})

    except ValidationError:
        await websocket.send_json({"op": "error", "detail": "Payload invalide"})


@router.websocket("/ws/whiteboard/{whiteboard_id}")
async def whiteboard_ws(websocket: WebSocket, whiteboard_id: str):
    if not WHITEBOARD_ID_RE.match(whiteboard_id):
        await websocket.close(code=1008)
        return

    # short-lived: just for the initial access check. A connection stays
    # open for as long as the tab does (potentially the whole class), so
    # holding one DB connection checked out from the pool for that entire
    # time — as this used to — starves every other request once enough
    # boards are open at once (pool exhausts, unrelated requests start
    # timing out with QueuePool errors). Each message instead gets its own
    # short-lived session below, released the moment it's handled.
    db = SessionLocal()
    try:
        board = db.get(Whiteboard, whiteboard_id)
        if board is None:
            await websocket.close(code=1008)
            return

        identity = get_ws_identity(websocket, db)
        if not check_board_access(identity, board.session_id, db):
            await websocket.close(code=1008)
            return
    finally:
        db.close()

    connection_id = await manager.connect(whiteboard_id, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            db = SessionLocal()
            try:
                await handle_message(whiteboard_id, raw, db, websocket, identity, connection_id)
            finally:
                db.close()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(whiteboard_id, websocket)
        # so a viewer's cursor disappears immediately when they leave,
        # instead of waiting out the other clients' own hide-timeout
        await manager.broadcast(whiteboard_id, {"op": "cursor_gone", "id": connection_id})
        editing_info = manager.pop_editing(whiteboard_id, websocket)
        if editing_info is not None:
            await manager.broadcast(
                whiteboard_id,
                {
                    "op": "editing",
                    "id": editing_info["id"],
                    "editing": False,
                    "cell": editing_info["cell"],
                    "editor_id": connection_id,
                },
            )
