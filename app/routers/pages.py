import json
import os
import re
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy.orm import Session

from app.activity_log import get_history, log_activity
from app.auth import Identity, get_identity, get_ws_identity
from app.db import DATA_DIR, SessionLocal, get_db
from app.models import ActivityLog, Page, PageBlock, PageBoard
from app.session_access import (
    check_board_access,
    check_session_ownership_for_attach,
    enforce_board_access_api,
    enforce_board_access_page,
)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

PageBoardIdPath = Path(pattern=r"^[0-9a-f]{32}$")
PAGE_BOARD_ID_RE = re.compile(r"^[0-9a-f]{32}$")

DEFAULT_FONT_SIZE = 16

# overridable for tests, same pattern as PROJECTMGR_MAX_WHITEBOARD_ELEMENTS in whiteboard.py
MAX_PAGES_PER_BOARD = int(os.environ.get("PROJECTMGR_MAX_PAGES_PER_BOARD") or 300)
MAX_NESTING_DEPTH = int(os.environ.get("PROJECTMGR_MAX_PAGE_NESTING_DEPTH") or 8)
MAX_BLOCKS_PER_PAGE = int(os.environ.get("PROJECTMGR_MAX_BLOCKS_PER_PAGE") or 200)

ALLOWED_BLOCK_TYPES = {"text", "image", "table", "link", "code"}
BLOCK_TYPE_LABELS = {"text": "texte", "image": "image", "table": "tableau", "link": "lien", "code": "code"}
ALLOWED_CODE_LANGUAGES = {"text", "python", "javascript", "html", "css", "sql", "bash", "json", "autre"}

UPLOAD_DIR = os.path.join(DATA_DIR, "uploads", "pages")
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


class TextRun(BaseModel):
    text: str = Field(default="", max_length=2000)
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    font_size: int = Field(default=DEFAULT_FONT_SIZE, ge=8, le=96)


class TextParagraph(BaseModel):
    bullet: bool = False
    heading: int = Field(default=0, ge=0, le=4)  # 0 = normal paragraph, 1-4 = H1..H4
    runs: list[TextRun] = Field(default_factory=list)

    @field_validator("runs")
    @classmethod
    def check_runs(cls, v: list[TextRun]) -> list[TextRun]:
        if len(v) > 100:
            raise ValueError("trop de segments dans ce paragraphe (max 100)")
        return v


class PageTextData(BaseModel):
    paragraphs: list[TextParagraph] = Field(default_factory=lambda: [TextParagraph(runs=[TextRun()])])
    # draws a border/box around the whole block — a block-level toggle, not
    # a per-run style, so it lives next to `paragraphs` rather than inside it
    framed: bool = False

    @field_validator("paragraphs")
    @classmethod
    def check_paragraphs(cls, v: list[TextParagraph]) -> list[TextParagraph]:
        if not v:
            raise ValueError("le texte doit avoir au moins un paragraphe")
        if len(v) > 200:
            raise ValueError("trop de paragraphes (max 200)")
        return v


class PageImageData(BaseModel):
    src: str = Field(max_length=500)
    caption: str = Field(default="", max_length=300)
    # None = natural size (100% of the content column); set once the user
    # drags the resize handle
    width: int | None = Field(default=None, ge=50, le=2000)

    @field_validator("src")
    @classmethod
    def check_src(cls, v: str) -> str:
        if not v.startswith("/uploads/pages/"):
            raise ValueError("src invalide : doit provenir de l'upload de ce document")
        return v


class TableCell(BaseModel):
    text: str = Field(default="", max_length=500)
    bold: bool = False
    italic: bool = False


class PageTableData(BaseModel):
    rows: list[list[TableCell]] = Field(default_factory=lambda: [[TableCell(), TableCell()] for _ in range(3)])
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


class PageLinkData(BaseModel):
    # no server-side preview fetch by design (see docs/specs.md decision) —
    # title/description are always typed by hand, never derived from
    # fetching the target URL, so there is no new SSRF surface here
    url: str = Field(default="", max_length=2000)
    title: str = Field(default="", max_length=300)
    description: str = Field(default="", max_length=500)

    @field_validator("url")
    @classmethod
    def check_url(cls, v: str) -> str:
        if not v:
            return v  # a freshly created link block starts empty
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("URL invalide (http/https uniquement)")
        return v


class PageCodeData(BaseModel):
    code: str = Field(default="", max_length=20_000)
    language: str = Field(default="text", max_length=30)

    @field_validator("language")
    @classmethod
    def check_language(cls, v: str) -> str:
        if v not in ALLOWED_CODE_LANGUAGES:
            raise ValueError(f"langage invalide (autorisés : {sorted(ALLOWED_CODE_LANGUAGES)})")
        return v


class BlockIn(BaseModel):
    type: str
    data: dict = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def check_type(cls, v: str) -> str:
        if v not in ALLOWED_BLOCK_TYPES:
            raise ValueError(f"type doit être l'un de {sorted(ALLOWED_BLOCK_TYPES)}")
        return v

    @model_validator(mode="after")
    def normalize_data(self):
        # every value check_type() accepts is handled below — no fallback
        # branch, so a new type can't silently skip data validation
        if self.type == "text":
            self.data = PageTextData(**self.data).model_dump()
        elif self.type == "image":
            self.data = PageImageData(**self.data).model_dump()
        elif self.type == "table":
            self.data = PageTableData(**self.data).model_dump()
        elif self.type == "link":
            self.data = PageLinkData(**self.data).model_dump()
        elif self.type == "code":
            self.data = PageCodeData(**self.data).model_dump()
        return self


class WsCreateBlockMsg(BaseModel):
    op: str = Field(pattern="^create_block$")
    page_id: int = Field(ge=1)
    block: BlockIn
    index: int | None = Field(default=None, ge=0, le=10_000)
    client_ref: str | None = Field(default=None, max_length=64)


class WsUpdateBlockMsg(BaseModel):
    op: str = Field(pattern="^update_block$")
    id: int = Field(ge=1)
    block: BlockIn


class WsMoveBlockMsg(BaseModel):
    op: str = Field(pattern="^move_block$")
    id: int = Field(ge=1)
    index: int = Field(ge=0, le=10_000)


class WsDeleteBlockMsg(BaseModel):
    op: str = Field(pattern="^delete_block$")
    id: int = Field(ge=1)


class WsCreatePageMsg(BaseModel):
    op: str = Field(pattern="^create_page$")
    parent_id: int | None = Field(default=None, ge=1)
    title: str = Field(default="Nouvelle page", max_length=200)
    index: int | None = Field(default=None, ge=0, le=10_000)
    client_ref: str | None = Field(default=None, max_length=64)


class WsRenamePageMsg(BaseModel):
    op: str = Field(pattern="^rename_page$")
    id: int = Field(ge=1)
    title: str = Field(max_length=200)


class WsMovePageMsg(BaseModel):
    op: str = Field(pattern="^move_page$")
    id: int = Field(ge=1)
    parent_id: int | None = Field(default=None, ge=1)
    index: int = Field(ge=0, le=10_000)


class WsDeletePageMsg(BaseModel):
    op: str = Field(pattern="^delete_page$")
    id: int = Field(ge=1)


class WsRestoreMsg(BaseModel):
    op: str = Field(pattern="^restore$")
    log_id: int = Field(ge=1)


def get_page_board_or_404(board_id: str, db: Session) -> PageBoard:
    board = db.get(PageBoard, board_id)
    if board is None:
        raise HTTPException(status_code=404, detail="Pages introuvable")
    return board


def serialize_page(page: Page) -> dict:
    return {
        "id": page.id,
        "parent_id": page.parent_id,
        "order_index": page.order_index,
        "title": page.title,
    }


def serialize_block(block: PageBlock) -> dict:
    return {
        "id": block.id,
        "page_id": block.page_id,
        "order_index": block.order_index,
        "type": block.type,
        "data": block.data,
    }


def _siblings(db: Session, board_id: str, parent_id: int | None, exclude_id: int | None = None) -> list[Page]:
    query = db.query(Page).filter(Page.board_id == board_id, Page.parent_id == parent_id)
    if exclude_id is not None:
        query = query.filter(Page.id != exclude_id)
    return query.order_by(Page.order_index).all()


def _block_siblings(db: Session, page_id: int, exclude_id: int | None = None) -> list[PageBlock]:
    query = db.query(PageBlock).filter(PageBlock.page_id == page_id)
    if exclude_id is not None:
        query = query.filter(PageBlock.id != exclude_id)
    return query.order_by(PageBlock.order_index).all()


def _ancestor_chain(start_id: int, db: Session) -> list[int]:
    """[start_id, its parent id, its grandparent id, ..., the root's id].

    Assumes start_id exists — callers must validate that first. Stops early
    (defensively) if it ever revisits an id, which should never happen on
    data this app wrote itself.
    """
    chain: list[int] = []
    current_id: int | None = start_id
    while current_id is not None and current_id not in chain:
        chain.append(current_id)
        page = db.get(Page, current_id)
        if page is None:
            break
        current_id = page.parent_id
    return chain


def _subtree_height(page_id: int, db: Session) -> int:
    """1 for a leaf, 1 + tallest child subtree otherwise."""
    page = db.get(Page, page_id)
    if page is None or not page.children:
        return 1
    return 1 + max(_subtree_height(c.id, db) for c in page.children)


def _serialize_subtree(page: Page) -> dict:
    """Recursive snapshot of a page, its blocks, and every descendant page
    (with its own blocks) — captured before a delete so the whole thing can
    be undone in one "restore", not just the one page the user clicked on."""
    return {
        **serialize_page(page),
        "blocks": [serialize_block(b) for b in page.blocks],
        "children": [_serialize_subtree(c) for c in page.children],
    }


def _collect_subtree_ids(page: Page) -> list[int]:
    ids = [page.id]
    for child in page.children:
        ids.extend(_collect_subtree_ids(child))
    return ids


def _count_subtree_pages(snapshot: dict) -> int:
    return 1 + sum(_count_subtree_pages(c) for c in snapshot.get("children", []))


def _subtree_snapshot_height(snapshot: dict) -> int:
    children = snapshot.get("children", [])
    if not children:
        return 1
    return 1 + max(_subtree_snapshot_height(c) for c in children)


def _subtree_snapshot_respects_block_cap(snapshot: dict) -> bool:
    if len(snapshot.get("blocks", [])) > MAX_BLOCKS_PER_PAGE:
        return False
    return all(_subtree_snapshot_respects_block_cap(c) for c in snapshot.get("children", []))


def _restore_subtree(
    db: Session,
    board_id: str,
    snapshot: dict,
    parent_id: int | None,
    created_pages: list[Page],
    created_blocks: list[PageBlock],
) -> Page:
    siblings = _siblings(db, board_id, parent_id)
    title = (snapshot.get("title") or "Nouvelle page")[:200]
    page = Page(board_id=board_id, parent_id=parent_id, title=title, order_index=len(siblings))
    db.add(page)
    db.flush()
    created_pages.append(page)

    for block_snapshot in snapshot.get("blocks", []):
        try:
            restored_block = BlockIn.model_validate(
                {"type": block_snapshot.get("type"), "data": block_snapshot.get("data") or {}}
            )
        except ValidationError:
            continue  # a block that no longer validates is skipped, not fatal to the whole restore
        block = PageBlock(
            page_id=page.id,
            type=restored_block.type,
            data=restored_block.data,
            order_index=block_snapshot.get("order_index", 0),
        )
        db.add(block)
        db.flush()
        created_blocks.append(block)

    for child_snapshot in snapshot.get("children", []):
        _restore_subtree(db, board_id, child_snapshot, page.id, created_pages, created_blocks)

    return page


@router.post("/pages/new")
def new_page_board(
    session_id: str | None = Form(default=None),
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    if session_id is not None:
        check_session_ownership_for_attach(identity, session_id, db)
    board = PageBoard(name="Nouvelles Pages", session_id=session_id)
    db.add(board)
    db.commit()
    return RedirectResponse(url=f"/pages/{board.id}", status_code=303)


@router.get("/pages/{board_id}")
def page_board_page(
    request: Request,
    board_id: str = PageBoardIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    board = get_page_board_or_404(board_id, db)
    enforce_board_access_page(identity, board.session_id, db)
    return templates.TemplateResponse(request, "pages.html", {"board": board, "identity": identity})


@router.get("/api/pages/{board_id}")
def page_board_json(
    board_id: str = PageBoardIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    board = get_page_board_or_404(board_id, db)
    enforce_board_access_api(identity, board.session_id, db)
    pages = db.query(Page).filter(Page.board_id == board_id).order_by(Page.parent_id, Page.order_index).all()
    page_ids = [p.id for p in pages]
    blocks = (
        db.query(PageBlock).filter(PageBlock.page_id.in_(page_ids)).order_by(PageBlock.page_id, PageBlock.order_index).all()
        if page_ids
        else []
    )
    return {
        "id": board.id,
        "name": board.name,
        "pages": [serialize_page(p) for p in pages],
        "blocks": [serialize_block(b) for b in blocks],
    }


@router.patch("/api/pages/{board_id}")
def rename_page_board(
    payload: RenameIn,
    board_id: str = PageBoardIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    board = get_page_board_or_404(board_id, db)
    enforce_board_access_api(identity, board.session_id, db)
    new_name = payload.name.strip() or board.name
    if new_name != board.name:
        log_activity(db, "pages", board_id, identity, "renamed", f'Pages renommé « {board.name} » → « {new_name} »')
        board.name = new_name
    db.commit()
    return {"ok": True, "name": board.name}


@router.get("/api/pages/{board_id}/history")
def page_board_history(
    board_id: str = PageBoardIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    board = get_page_board_or_404(board_id, db)
    enforce_board_access_api(identity, board.session_id, db)
    return {"entries": get_history(db, "pages", board_id)}


def _store_page_image(board_id: str, content: bytes, ext: str) -> str:
    board_dir = os.path.join(UPLOAD_DIR, board_id)
    os.makedirs(board_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(board_dir, filename), "wb") as f:
        f.write(content)
    return f"/uploads/pages/{board_id}/{filename}"


@router.post("/api/pages/{board_id}/upload-image")
async def upload_image(
    board_id: str = PageBoardIdPath,
    file: UploadFile = File(...),
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    board = get_page_board_or_404(board_id, db)
    enforce_board_access_api(identity, board.session_id, db)

    content = await file.read(MAX_IMAGE_BYTES + 1)
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image trop volumineuse (5 Mo maximum)")
    if not content:
        raise HTTPException(status_code=400, detail="Fichier vide")

    ext = sniff_image_ext(content)
    if ext is None:
        raise HTTPException(status_code=415, detail="Type d'image non supporté (png, jpeg, webp, gif uniquement)")

    return {"url": _store_page_image(board_id, content, ext)}


@router.get("/uploads/pages/{board_id}/{filename}")
def get_uploaded_image(
    board_id: str = PageBoardIdPath,
    filename: str = Path(pattern=UPLOAD_FILENAME_PATTERN),
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    board = get_page_board_or_404(board_id, db)
    enforce_board_access_api(identity, board.session_id, db)
    path = os.path.join(UPLOAD_DIR, board_id, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Image introuvable")
    ext = filename.rsplit(".", 1)[-1]
    return FileResponse(path, media_type=IMAGE_MEDIA_TYPES[ext])


class ConnectionManager:
    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = {}

    async def connect(self, board_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._rooms.setdefault(board_id, set()).add(websocket)

    def disconnect(self, board_id: str, websocket: WebSocket) -> None:
        room = self._rooms.get(board_id)
        if room is not None:
            room.discard(websocket)
            if not room:
                self._rooms.pop(board_id, None)

    async def broadcast(self, board_id: str, message: dict) -> None:
        dead = []
        for ws in self._rooms.get(board_id, set()):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(board_id, ws)


manager = ConnectionManager()


async def handle_message(board_id: str, raw: str, db: Session, websocket: WebSocket, identity: Identity) -> None:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        await websocket.send_json({"op": "error", "detail": "JSON invalide"})
        return

    op = payload.get("op") if isinstance(payload, dict) else None

    try:
        if op == "create_page":
            msg = WsCreatePageMsg.model_validate(payload)
            if msg.parent_id is not None:
                parent = db.get(Page, msg.parent_id)
                if parent is None or parent.board_id != board_id:
                    await websocket.send_json({"op": "error", "detail": "Page parente introuvable"})
                    return
                depth = len(_ancestor_chain(msg.parent_id, db)) + 1
            else:
                depth = 1
            if depth > MAX_NESTING_DEPTH:
                await websocket.send_json(
                    {"op": "error", "detail": f"Profondeur maximale dépassée (max {MAX_NESTING_DEPTH})"}
                )
                return
            total = db.query(Page).filter(Page.board_id == board_id).count()
            if total >= MAX_PAGES_PER_BOARD:
                await websocket.send_json({"op": "error", "detail": f"Trop de pages (max {MAX_PAGES_PER_BOARD})"})
                return

            siblings = _siblings(db, board_id, msg.parent_id)
            index = len(siblings) if msg.index is None else max(0, min(msg.index, len(siblings)))
            page = Page(board_id=board_id, parent_id=msg.parent_id, title=msg.title, order_index=index)
            db.add(page)
            db.flush()
            siblings.insert(index, page)
            for i, p in enumerate(siblings):
                p.order_index = i
            log_activity(db, "pages", board_id, identity, "created", f'Page « {msg.title} » créée')
            db.commit()
            db.refresh(page)
            await manager.broadcast(
                board_id, {"op": "page_created", "page": serialize_page(page), "client_ref": msg.client_ref}
            )

        elif op == "rename_page":
            msg = WsRenamePageMsg.model_validate(payload)
            page = db.get(Page, msg.id)
            if page is None or page.board_id != board_id:
                await websocket.send_json({"op": "error", "detail": "Page introuvable"})
                return
            if msg.title != page.title:
                log_activity(
                    db, "pages", board_id, identity, "renamed", f'Page renommée « {page.title} » → « {msg.title} »'
                )
                page.title = msg.title
            db.commit()
            await manager.broadcast(board_id, {"op": "page_renamed", "id": page.id, "title": page.title})

        elif op == "move_page":
            msg = WsMovePageMsg.model_validate(payload)
            page = db.get(Page, msg.id)
            if page is None or page.board_id != board_id:
                await websocket.send_json({"op": "error", "detail": "Page introuvable"})
                return

            new_parent_id = msg.parent_id
            if new_parent_id is not None:
                new_parent = db.get(Page, new_parent_id)
                if new_parent is None or new_parent.board_id != board_id:
                    await websocket.send_json({"op": "error", "detail": "Page parente introuvable"})
                    return
                chain = _ancestor_chain(new_parent_id, db)
                if page.id in chain:
                    await websocket.send_json(
                        {"op": "error", "detail": "Une page ne peut pas devenir sa propre descendante"}
                    )
                    return
                new_depth = len(chain) + 1
            else:
                new_depth = 1
            height = _subtree_height(page.id, db)
            if new_depth + height - 1 > MAX_NESTING_DEPTH:
                await websocket.send_json(
                    {"op": "error", "detail": f"Profondeur maximale dépassée (max {MAX_NESTING_DEPTH})"}
                )
                return

            old_parent_id = page.parent_id
            dest_siblings = _siblings(db, board_id, new_parent_id, exclude_id=page.id)
            index = max(0, min(msg.index, len(dest_siblings)))
            dest_siblings.insert(index, page)
            page.parent_id = new_parent_id
            for i, p in enumerate(dest_siblings):
                p.order_index = i

            affected = [{"parent_id": new_parent_id, "order": [p.id for p in dest_siblings]}]
            if old_parent_id != new_parent_id:
                old_siblings = _siblings(db, board_id, old_parent_id)
                for i, p in enumerate(old_siblings):
                    p.order_index = i
                affected.append({"parent_id": old_parent_id, "order": [p.id for p in old_siblings]})
                log_activity(db, "pages", board_id, identity, "updated", f'Page « {page.title} » déplacée')

            db.commit()
            await manager.broadcast(
                board_id, {"op": "page_moved", "id": page.id, "parent_id": page.parent_id, "affected": affected}
            )

        elif op == "delete_page":
            msg = WsDeletePageMsg.model_validate(payload)
            page = db.get(Page, msg.id)
            if page is None or page.board_id != board_id:
                await websocket.send_json({"op": "error", "detail": "Page introuvable"})
                return
            # captured before the delete, with every descendant page and all
            # of their blocks, so the whole subtree can be undone in one
            # "restore" rather than only the one page that got clicked on
            snapshot = _serialize_subtree(page)
            deleted_ids = _collect_subtree_ids(page)
            had_children = bool(page.children)
            title = page.title
            parent_id = page.parent_id
            db.delete(page)
            siblings = _siblings(db, board_id, parent_id)
            for i, p in enumerate(siblings):
                p.order_index = i
            summary = f'Page « {title} » supprimée'
            if had_children:
                summary += " (avec ses sous-pages)"
            log_activity(db, "pages", board_id, identity, "deleted", summary, payload=snapshot)
            db.commit()
            await manager.broadcast(
                board_id, {"op": "page_deleted", "id": msg.id, "deleted_ids": deleted_ids, "order": [p.id for p in siblings]}
            )

        elif op == "create_block":
            msg = WsCreateBlockMsg.model_validate(payload)
            page = db.get(Page, msg.page_id)
            if page is None or page.board_id != board_id:
                await websocket.send_json({"op": "error", "detail": "Page introuvable"})
                return
            count = db.query(PageBlock).filter(PageBlock.page_id == msg.page_id).count()
            if count >= MAX_BLOCKS_PER_PAGE:
                await websocket.send_json({"op": "error", "detail": f"Trop de blocs sur cette page (max {MAX_BLOCKS_PER_PAGE})"})
                return

            siblings = _block_siblings(db, msg.page_id)
            index = len(siblings) if msg.index is None else max(0, min(msg.index, len(siblings)))
            block = PageBlock(page_id=msg.page_id, type=msg.block.type, data=msg.block.data, order_index=index)
            db.add(block)
            db.flush()
            siblings.insert(index, block)
            for i, b in enumerate(siblings):
                b.order_index = i
            log_activity(db, "pages", board_id, identity, "created", f"Bloc {BLOCK_TYPE_LABELS[msg.block.type]} ajouté")
            db.commit()
            db.refresh(block)
            await manager.broadcast(
                board_id, {"op": "block_created", "block": serialize_block(block), "client_ref": msg.client_ref}
            )

        elif op == "update_block":
            msg = WsUpdateBlockMsg.model_validate(payload)
            block = db.get(PageBlock, msg.id)
            if block is None or block.page.board_id != board_id:
                await websocket.send_json({"op": "error", "detail": "Bloc introuvable"})
                return
            if msg.block.type != block.type:
                # same invariant as whiteboard.py's "update": a block's data
                # is only validated against the type it declares — silently
                # accepting a different type would let an update retype it
                # and reshape its data out from under its own schema
                await websocket.send_json({"op": "error", "detail": "Impossible de changer le type d'un bloc existant"})
                return
            block.data = msg.block.data
            log_activity(db, "pages", board_id, identity, "updated", f"Bloc {BLOCK_TYPE_LABELS[block.type]} modifié")
            db.commit()
            await manager.broadcast(board_id, {"op": "block_updated", "block": serialize_block(block)})

        elif op == "move_block":
            msg = WsMoveBlockMsg.model_validate(payload)
            block = db.get(PageBlock, msg.id)
            if block is None or block.page.board_id != board_id:
                await websocket.send_json({"op": "error", "detail": "Bloc introuvable"})
                return
            siblings = _block_siblings(db, block.page_id, exclude_id=block.id)
            index = max(0, min(msg.index, len(siblings)))
            siblings.insert(index, block)
            for i, b in enumerate(siblings):
                b.order_index = i
            db.commit()
            await manager.broadcast(
                board_id, {"op": "block_moved", "page_id": block.page_id, "order": [b.id for b in siblings]}
            )

        elif op == "delete_block":
            msg = WsDeleteBlockMsg.model_validate(payload)
            block = db.get(PageBlock, msg.id)
            if block is None or block.page.board_id != board_id:
                await websocket.send_json({"op": "error", "detail": "Bloc introuvable"})
                return
            snapshot = serialize_block(block)
            block_type = block.type
            page_id = block.page_id
            db.delete(block)
            siblings = _block_siblings(db, page_id)
            for i, b in enumerate(siblings):
                b.order_index = i
            log_activity(
                db, "pages", board_id, identity, "deleted", f"Bloc {BLOCK_TYPE_LABELS[block_type]} supprimé", payload=snapshot
            )
            db.commit()
            await manager.broadcast(
                board_id, {"op": "block_deleted", "id": msg.id, "page_id": page_id, "order": [b.id for b in siblings]}
            )

        elif op == "restore":
            msg = WsRestoreMsg.model_validate(payload)
            entry = db.get(ActivityLog, msg.log_id)
            if (
                entry is None
                or entry.tool_type != "pages"
                or entry.tool_id != board_id
                or entry.action != "deleted"
                or not entry.payload
            ):
                await websocket.send_json({"op": "error", "detail": "Rien à restaurer pour cette entrée"})
                return
            snapshot = entry.payload

            if "type" in snapshot:
                # a deleted block's snapshot (see serialize_block) — distinct
                # shape from a deleted page's (see serialize_page), which
                # never has a "type" key
                page = db.get(Page, snapshot.get("page_id"))
                if page is None or page.board_id != board_id:
                    await websocket.send_json({"op": "error", "detail": "La page d'origine n'existe plus"})
                    return
                count = db.query(PageBlock).filter(PageBlock.page_id == page.id).count()
                if count >= MAX_BLOCKS_PER_PAGE:
                    await websocket.send_json(
                        {"op": "error", "detail": f"Trop de blocs sur cette page (max {MAX_BLOCKS_PER_PAGE})"}
                    )
                    return
                try:
                    restored_block = BlockIn.model_validate(
                        {"type": snapshot.get("type"), "data": snapshot.get("data") or {}}
                    )
                except ValidationError:
                    await websocket.send_json({"op": "error", "detail": "Impossible de restaurer ce bloc"})
                    return
                siblings = _block_siblings(db, page.id)
                block = PageBlock(
                    page_id=page.id, type=restored_block.type, data=restored_block.data, order_index=len(siblings)
                )
                db.add(block)
                log_activity(
                    db,
                    "pages",
                    board_id,
                    identity,
                    "created",
                    f"Bloc {BLOCK_TYPE_LABELS[restored_block.type]} restauré depuis l'historique",
                )
                db.commit()
                db.refresh(block)
                await manager.broadcast(
                    board_id, {"op": "block_created", "block": serialize_block(block), "client_ref": None}
                )
                return

            # a deleted page's snapshot (see _serialize_subtree) — may carry
            # its own descendants (each with their own blocks) when the
            # original delete took a whole subtree with it
            pages_to_add = _count_subtree_pages(snapshot)
            total = db.query(Page).filter(Page.board_id == board_id).count()
            if total + pages_to_add > MAX_PAGES_PER_BOARD:
                await websocket.send_json({"op": "error", "detail": f"Trop de pages (max {MAX_PAGES_PER_BOARD})"})
                return
            if not _subtree_snapshot_respects_block_cap(snapshot):
                await websocket.send_json(
                    {"op": "error", "detail": f"Trop de blocs sur une page (max {MAX_BLOCKS_PER_PAGE})"}
                )
                return

            parent_id = snapshot.get("parent_id")
            if parent_id is not None:
                parent = db.get(Page, parent_id)
                if parent is None or parent.board_id != board_id:
                    parent_id = None  # original parent is gone — fall back to root rather than fail the restore
            new_depth = 1 if parent_id is None else len(_ancestor_chain(parent_id, db)) + 1
            if new_depth + _subtree_snapshot_height(snapshot) - 1 > MAX_NESTING_DEPTH:
                await websocket.send_json(
                    {"op": "error", "detail": f"Profondeur maximale dépassée (max {MAX_NESTING_DEPTH})"}
                )
                return

            created_pages: list[Page] = []
            created_blocks: list[PageBlock] = []
            root = _restore_subtree(db, board_id, snapshot, parent_id, created_pages, created_blocks)
            summary = f'Page « {root.title} » restaurée depuis l\'historique'
            if snapshot.get("children"):
                summary += " (avec ses sous-pages)"
            log_activity(db, "pages", board_id, identity, "created", summary)
            db.commit()
            for p in created_pages:
                db.refresh(p)
            for b in created_blocks:
                db.refresh(b)
            await manager.broadcast(
                board_id,
                {
                    "op": "subtree_restored",
                    "pages": [serialize_page(p) for p in created_pages],
                    "blocks": [serialize_block(b) for b in created_blocks],
                },
            )

        else:
            await websocket.send_json({"op": "error", "detail": "Opération inconnue"})

    except ValidationError:
        await websocket.send_json({"op": "error", "detail": "Payload invalide"})


@router.websocket("/ws/pages/{board_id}")
async def pages_ws(websocket: WebSocket, board_id: str):
    if not PAGE_BOARD_ID_RE.match(board_id):
        await websocket.close(code=1008)
        return

    # short-lived: just for the initial access check — see the matching
    # comment in whiteboard.py's whiteboard_ws for why a connection must
    # never hold one DB session checked out for its entire (potentially
    # hours-long) lifetime instead of one per message.
    db = SessionLocal()
    try:
        board = db.get(PageBoard, board_id)
        if board is None:
            await websocket.close(code=1008)
            return

        identity = get_ws_identity(websocket, db)
        if not check_board_access(identity, board.session_id, db):
            await websocket.close(code=1008)
            return
    finally:
        db.close()

    await manager.connect(board_id, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            db = SessionLocal()
            try:
                await handle_message(board_id, raw, db, websocket, identity)
            finally:
                db.close()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(board_id, websocket)
