import json
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Path, Request, WebSocket, WebSocketDisconnect
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app import courses, qcm
from app.auth import Identity, get_identity, get_ws_identity
from app.db import SessionLocal, get_db
from app.models import CoursePresentation, CourseSession, QcmAnswer, QcmSession, SessionMembership, User
from app.session_access import check_board_access, enforce_board_access_api, enforce_board_access_page

router = APIRouter()
templates = Jinja2Templates(directory="templates")

SessionIdPath = Path(pattern=r"^[A-Z0-9]{7}$")
SESSION_ID_RE = re.compile(r"^[A-Z0-9]{7}$")
QcmSessionIdPath = Path(ge=1)


class SelectTopicMsg(BaseModel):
    op: str = Field(pattern="^select_topic$")
    topic: str = Field(max_length=200)


class GotoMsg(BaseModel):
    op: str = Field(pattern="^goto$")
    slide_id: int = Field(ge=0)


class QcmPrepareMsg(BaseModel):
    op: str = Field(pattern="^qcm_prepare$")
    chapter_title: str = Field(max_length=200)
    seconds_per_question: int = Field(default=10, ge=3, le=120)


class QcmStartMsg(BaseModel):
    op: str = Field(pattern="^qcm_start$")


class QcmStopMsg(BaseModel):
    op: str = Field(pattern="^qcm_stop$")


class QcmAnswerMsg(BaseModel):
    op: str = Field(pattern="^qcm_answer$")
    question_index: int = Field(ge=0)
    option_indices: list[int] = Field(default_factory=list, max_length=50)
    action: str = Field(pattern="^(validate|defer)$")


def get_session_or_404(session_id: str, db: Session) -> CourseSession:
    session = db.get(CourseSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session introuvable")
    return session


def is_owner_of(identity: Identity, session: CourseSession) -> bool:
    return identity.is_admin or (identity.user is not None and identity.user.id == session.teacher_id)


def get_or_create_presentation(db: Session, session_id: str) -> CoursePresentation:
    pres = db.get(CoursePresentation, session_id)
    if pres is None:
        pres = CoursePresentation(session_id=session_id, topic=None, current_slide_id=None, visited_slide_ids=[])
        db.add(pres)
        db.flush()
    return pres


def serialize_presentation(pres: CoursePresentation) -> dict:
    return {
        "topic": pres.topic,
        "current_slide_id": pres.current_slide_id,
        "visited_slide_ids": pres.visited_slide_ids or [],
    }


def get_session_members(db: Session, session_id: str) -> list[User]:
    return (
        db.query(User)
        .join(SessionMembership, SessionMembership.user_id == User.id)
        .filter(SessionMembership.session_id == session_id)
        .order_by(User.username)
        .all()
    )


# --- QCM state machine -----------------------------------------------------
#
# No background scheduler ticks the "temps imparti" deadline on its own —
# instead, every websocket message on a session's /ws/cours/ connection
# opportunistically checks whether the active QCM's time is up and, if so,
# flips it to "ended" and broadcasts that right away. Good enough without a
# real scheduler since a live QCM always has *some* traffic (students
# answering, or just their per-question local countdowns eventually
# prompting one), and this app already commits to single-worker in-memory
# state elsewhere (see the login rate-limiter).


def get_current_qcm(db: Session, session_id: str) -> QcmSession | None:
    return (
        db.query(QcmSession)
        .filter(QcmSession.session_id == session_id)
        .order_by(QcmSession.id.desc())
        .first()
    )


def sweep_if_expired(db: Session, qs: QcmSession | None) -> bool:
    if qs is not None and qs.status == "running" and qs.ends_at is not None and datetime.utcnow() >= qs.ends_at:
        qs.status = "ended"
        qs.ended_at = qs.ends_at
        db.commit()
        return True
    return False


def get_active_qcm(db: Session, session_id: str) -> QcmSession | None:
    qs = get_current_qcm(db, session_id)
    sweep_if_expired(db, qs)
    if qs is not None and qs.status in ("pending", "running"):
        return qs
    return None


def build_qcm_payload(db: Session, qs: QcmSession, is_owner: bool, user_id: str | None) -> dict:
    try:
        questions = qcm.get_chapter_questions(qs.topic, qs.chapter_title)
    except qcm.QcmNotFoundError:
        questions = []

    base = {
        "qcm_session_id": qs.id,
        "chapter_title": qs.chapter_title,
        "status": qs.status,
        "seconds_per_question": qs.seconds_per_question,
        "question_count": len(questions),
        "ends_at": qs.ends_at.isoformat() if qs.ends_at else None,
    }

    answers = db.query(QcmAnswer).filter(QcmAnswer.qcm_session_id == qs.id).all()
    progress_by_user: dict[str, dict[int, str]] = {}
    for a in answers:
        progress_by_user.setdefault(a.user_id, {})[a.question_index] = a.status

    if is_owner:
        online_ids = manager.connected_user_ids(qs.session_id)
        members = get_session_members(db, qs.session_id)
        roster = [
            {
                "user_id": u.id,
                "username": u.username,
                "online": u.id in online_ids,
                "progress": progress_by_user.get(u.id, {}),
            }
            for u in members
        ]
        return {**base, "roster": roster}

    sanitized = [qcm.sanitize_question(q, i) for i, q in enumerate(questions)]
    return {**base, "questions": sanitized, "own_progress": progress_by_user.get(user_id, {}) if user_id else {}}


@router.get("/sessions/{id}/cours")
def cours_page(
    request: Request,
    id: str = SessionIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    session = get_session_or_404(id, db)
    enforce_board_access_page(identity, session.id, db)
    is_owner = is_owner_of(identity, session)

    pres = db.get(CoursePresentation, id)
    content = None
    qcm_available: dict[str, int] = {}
    if pres is not None and pres.topic:
        try:
            content = courses.parse_topic(pres.topic)
        except courses.TopicNotFoundError:
            content = None
        qcm_available = qcm.available_chapters(pres.topic)

    active_qcm = get_active_qcm(db, session.id)
    active_qcm_state = None
    if active_qcm is not None:
        active_qcm_state = build_qcm_payload(db, active_qcm, is_owner, identity.id)

    return templates.TemplateResponse(
        request,
        "cours.html",
        {
            "identity": identity,
            "session": session,
            "is_owner": is_owner,
            "topics": courses.list_topics(),
            "presentation": serialize_presentation(pres) if pres else {"topic": None, "current_slide_id": None, "visited_slide_ids": []},
            "content": content,
            "qcm_available": qcm_available,
            "active_qcm_state": active_qcm_state,
        },
    )


@router.get("/api/cours/{id}/content")
def cours_content(
    id: str = SessionIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    session = get_session_or_404(id, db)
    enforce_board_access_api(identity, session.id, db)
    pres = db.get(CoursePresentation, id)
    if pres is None or not pres.topic:
        raise HTTPException(status_code=404, detail="Aucun cours sélectionné")
    try:
        content = courses.parse_topic(pres.topic)
    except courses.TopicNotFoundError:
        raise HTTPException(status_code=404, detail="Cours introuvable")
    return {
        **serialize_presentation(pres),
        "tree": content["tree"],
        "slides": content["slides"],
        "title": content["title"],
        "qcm_available": qcm.available_chapters(pres.topic),
    }


@router.get("/sessions/{id}/qcm/{qcm_session_id}/results")
def qcm_results_page(
    request: Request,
    id: str = SessionIdPath,
    qcm_session_id: int = QcmSessionIdPath,
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_db),
):
    session = get_session_or_404(id, db)
    enforce_board_access_page(identity, session.id, db)
    if not is_owner_of(identity, session):
        raise HTTPException(status_code=403, detail="Réservé à l'enseignant de la session")

    qs = db.get(QcmSession, qcm_session_id)
    if qs is None or qs.session_id != session.id:
        raise HTTPException(status_code=404, detail="QCM introuvable")

    try:
        questions = qcm.get_chapter_questions(qs.topic, qs.chapter_title)
    except qcm.QcmNotFoundError:
        questions = []

    members = get_session_members(db, session.id)
    answers = db.query(QcmAnswer).filter(QcmAnswer.qcm_session_id == qs.id).all()
    answers_by_user: dict[str, dict[int, QcmAnswer]] = {}
    for a in answers:
        answers_by_user.setdefault(a.user_id, {})[a.question_index] = a

    question_stats = []
    for i, q in enumerate(questions):
        option_counts = [0] * len(q["options"])
        for per_user in answers_by_user.values():
            a = per_user.get(i)
            if a and a.status == "validated":
                for opt_i in a.selected_options:
                    if 0 <= opt_i < len(option_counts):
                        option_counts[opt_i] += 1
        question_stats.append(
            {
                "label": q["label"],
                "text": q["text"],
                "options": [
                    {"text": o["text"], "correct": o["correct"], "count": option_counts[oi]}
                    for oi, o in enumerate(q["options"])
                ],
            }
        )

    student_rows = []
    for u in members:
        per_user = answers_by_user.get(u.id, {})
        n_validated = sum(1 for a in per_user.values() if a.status == "validated")
        n_correct = 0
        for i, q in enumerate(questions):
            a = per_user.get(i)
            if a and a.status == "validated":
                correct_set = {oi for oi, o in enumerate(q["options"]) if o["correct"]}
                if set(a.selected_options) == correct_set:
                    n_correct += 1
        student_rows.append({"username": u.username, "answered": n_validated, "total": len(questions), "score": n_correct})

    return templates.TemplateResponse(
        request,
        "qcm_results.html",
        {
            "identity": identity,
            "session": session,
            "qs": qs,
            "question_stats": question_stats,
            "student_rows": student_rows,
        },
    )


class ConnectionManager:
    def __init__(self) -> None:
        self._rooms: dict[str, dict[WebSocket, dict]] = {}

    async def connect(self, session_id: str, websocket: WebSocket, user_id: str, is_owner: bool) -> None:
        await websocket.accept()
        self._rooms.setdefault(session_id, {})[websocket] = {"user_id": user_id, "is_owner": is_owner}

    def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        room = self._rooms.get(session_id)
        if room is not None:
            room.pop(websocket, None)
            if not room:
                self._rooms.pop(session_id, None)

    def connected_user_ids(self, session_id: str) -> set[str]:
        return {meta["user_id"] for meta in self._rooms.get(session_id, {}).values()}

    async def send_per_connection(self, session_id: str, builder) -> None:
        dead = []
        for ws, meta in list(self._rooms.get(session_id, {}).items()):
            message = builder(meta)
            if message is None:
                continue
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(session_id, ws)

    async def broadcast(self, session_id: str, message: dict) -> None:
        await self.send_per_connection(session_id, lambda meta: message)


manager = ConnectionManager()


async def broadcast_qcm_state(db: Session, qs: QcmSession) -> None:
    def builder(meta):
        return {"op": "qcm_state", **build_qcm_payload(db, qs, meta["is_owner"], meta["user_id"])}

    await manager.send_per_connection(qs.session_id, builder)


async def handle_message(session_id: str, raw: str, db: Session, websocket: WebSocket, identity: Identity, is_owner: bool) -> None:
    # `db` is a fresh, short-lived session opened just for this one message
    # (see cours_ws) — always starts a new transaction, so there's no
    # leftover staleness from a previous message to worry about here.
    current_qcm = get_current_qcm(db, session_id)
    if sweep_if_expired(db, current_qcm):
        await broadcast_qcm_state(db, current_qcm)

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        await websocket.send_json({"op": "error", "detail": "JSON invalide"})
        return

    op = payload.get("op") if isinstance(payload, dict) else None

    # only the session's teacher (or an admin) drives the presentation and
    # the QCM lifecycle — students' navigation among already-visited slides
    # never touches the server, and only they may answer a running QCM
    owner_only_ops = {"select_topic", "goto", "qcm_prepare", "qcm_start", "qcm_stop"}
    if op in owner_only_ops and not is_owner:
        await websocket.send_json({"op": "error", "detail": "Réservé à l'enseignant de la session"})
        return
    if op == "qcm_answer" and is_owner:
        await websocket.send_json({"op": "error", "detail": "Réservé aux élèves"})
        return

    try:
        if op == "select_topic":
            msg = SelectTopicMsg.model_validate(payload)
            if not courses.topic_exists(msg.topic):
                await websocket.send_json({"op": "error", "detail": "Cours introuvable"})
                return
            pres = get_or_create_presentation(db, session_id)
            pres.topic = msg.topic
            pres.current_slide_id = 0
            pres.visited_slide_ids = [0]
            db.commit()
            await manager.broadcast(session_id, {"op": "topic_changed", **serialize_presentation(pres)})

        elif op == "goto":
            msg = GotoMsg.model_validate(payload)
            pres = get_or_create_presentation(db, session_id)
            if not pres.topic:
                await websocket.send_json({"op": "error", "detail": "Aucun cours sélectionné"})
                return
            try:
                content = courses.parse_topic(pres.topic)
            except courses.TopicNotFoundError:
                await websocket.send_json({"op": "error", "detail": "Cours introuvable"})
                return
            if msg.slide_id not in content["slides"]:
                await websocket.send_json({"op": "error", "detail": "Diapositive introuvable"})
                return
            pres.current_slide_id = msg.slide_id
            visited = set(pres.visited_slide_ids or [])
            visited.add(msg.slide_id)
            pres.visited_slide_ids = sorted(visited)
            db.commit()
            await manager.broadcast(session_id, {"op": "slide_changed", **serialize_presentation(pres)})

        elif op == "qcm_prepare":
            msg = QcmPrepareMsg.model_validate(payload)
            pres = get_or_create_presentation(db, session_id)
            if not pres.topic:
                await websocket.send_json({"op": "error", "detail": "Aucun cours sélectionné"})
                return
            if get_active_qcm(db, session_id) is not None:
                await websocket.send_json({"op": "error", "detail": "Un QCM est déjà en cours pour cette session"})
                return
            if msg.chapter_title not in qcm.available_chapters(pres.topic):
                await websocket.send_json({"op": "error", "detail": "Aucun QCM pour ce chapitre"})
                return
            qs = QcmSession(
                session_id=session_id,
                topic=pres.topic,
                chapter_title=msg.chapter_title,
                seconds_per_question=msg.seconds_per_question,
                status="pending",
            )
            db.add(qs)
            db.commit()
            db.refresh(qs)
            await broadcast_qcm_state(db, qs)

        elif op == "qcm_start":
            QcmStartMsg.model_validate(payload)
            qs = get_active_qcm(db, session_id)
            if qs is None or qs.status != "pending":
                await websocket.send_json({"op": "error", "detail": "Aucun QCM en attente de démarrage"})
                return
            try:
                questions = qcm.get_chapter_questions(qs.topic, qs.chapter_title)
            except qcm.QcmNotFoundError:
                await websocket.send_json({"op": "error", "detail": "QCM introuvable"})
                return
            now = datetime.utcnow()
            qs.status = "running"
            qs.started_at = now
            qs.ends_at = now + timedelta(seconds=qs.seconds_per_question * max(1, len(questions)))
            db.commit()
            await broadcast_qcm_state(db, qs)

        elif op == "qcm_stop":
            QcmStopMsg.model_validate(payload)
            qs = get_active_qcm(db, session_id)
            if qs is None:
                await websocket.send_json({"op": "error", "detail": "Aucun QCM actif"})
                return
            qs.status = "ended"
            qs.ended_at = datetime.utcnow()
            db.commit()
            await broadcast_qcm_state(db, qs)

        elif op == "qcm_answer":
            msg = QcmAnswerMsg.model_validate(payload)
            if identity.user is None:
                await websocket.send_json({"op": "error", "detail": "Compte élève requis"})
                return
            qs = get_active_qcm(db, session_id)
            if qs is None or qs.status != "running":
                await websocket.send_json({"op": "error", "detail": "Aucun QCM en cours"})
                return
            try:
                questions = qcm.get_chapter_questions(qs.topic, qs.chapter_title)
            except qcm.QcmNotFoundError:
                await websocket.send_json({"op": "error", "detail": "QCM introuvable"})
                return
            if msg.question_index >= len(questions):
                await websocket.send_json({"op": "error", "detail": "Question introuvable"})
                return
            n_options = len(questions[msg.question_index]["options"])
            if any(i < 0 or i >= n_options for i in msg.option_indices):
                await websocket.send_json({"op": "error", "detail": "Réponse invalide"})
                return

            answer = (
                db.query(QcmAnswer)
                .filter_by(qcm_session_id=qs.id, user_id=identity.user.id, question_index=msg.question_index)
                .first()
            )
            if answer is None:
                answer = QcmAnswer(qcm_session_id=qs.id, user_id=identity.user.id, question_index=msg.question_index)
                db.add(answer)
            if msg.action == "validate":
                answer.status = "validated"
                answer.selected_options = sorted(set(msg.option_indices))
            else:
                answer.status = "deferred"
                answer.selected_options = []
            db.commit()

            await manager.broadcast(
                session_id,
                {
                    "op": "qcm_progress",
                    "user_id": identity.user.id,
                    "question_index": msg.question_index,
                    "status": answer.status,
                },
            )

        else:
            await websocket.send_json({"op": "error", "detail": "Opération inconnue"})

    except ValidationError:
        await websocket.send_json({"op": "error", "detail": "Payload invalide"})


@router.websocket("/ws/cours/{session_id}")
async def cours_ws(websocket: WebSocket, session_id: str):
    if not SESSION_ID_RE.match(session_id):
        await websocket.close(code=1008)
        return

    # every DB access below opens and closes its own short-lived session
    # rather than holding one for this connection's entire lifetime (which
    # can be hours) — see the matching comment in whiteboard.py's
    # whiteboard_ws for why: a long-held session starves the pool once
    # enough tabs are open at once, and every *other* request (including
    # this app's own presence heartbeat) starts timing out with QueuePool
    # errors.
    db = SessionLocal()
    try:
        session = db.get(CourseSession, session_id)
        if session is None:
            await websocket.close(code=1008)
            return

        identity = get_ws_identity(websocket, db)
        if not check_board_access(identity, session.id, db):
            await websocket.close(code=1008)
            return
        is_owner = is_owner_of(identity, session)
    finally:
        db.close()

    await manager.connect(session_id, websocket, identity.id, is_owner)

    db = SessionLocal()
    try:
        current_qcm = get_current_qcm(db, session_id)
        just_ended = sweep_if_expired(db, current_qcm)
        if current_qcm is not None and (current_qcm.status in ("pending", "running") or just_ended):
            await broadcast_qcm_state(db, current_qcm)
    finally:
        db.close()

    try:
        while True:
            raw = await websocket.receive_text()
            db = SessionLocal()
            try:
                await handle_message(session_id, raw, db, websocket, identity, is_owner)
            finally:
                db.close()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(session_id, websocket)
        db = SessionLocal()
        try:
            active_qcm = get_active_qcm(db, session_id)
            if active_qcm is not None:
                await broadcast_qcm_state(db, active_qcm)
        finally:
            db.close()
