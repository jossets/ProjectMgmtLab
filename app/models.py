import secrets
import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def new_uuid() -> str:
    return uuid.uuid4().hex


# unambiguous alphabet for join codes read aloud in class: no 0/O, 1/I
JOIN_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_join_code() -> str:
    return "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(7))


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))  # "teacher" | "student"
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CourseSession(Base):
    # named CourseSession (table course_sessions), never just "Session" —
    # that name is already taken by request.session, the unrelated HTTP
    # cookie session from SessionMiddleware
    __tablename__ = "course_sessions"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)  # the join code itself, e.g. "3D6GH75"
    name: Mapped[str] = mapped_column(String(200), default="Nouvelle session")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_new_accounts: Mapped[bool] = mapped_column(Boolean, default=True)
    # nullable: a session created by the config-based admin has no row in
    # `users` to point to — identity checks fall back to Identity.is_admin
    teacher_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    memberships: Mapped[list["SessionMembership"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class SessionMembership(Base):
    __tablename__ = "session_memberships"
    __table_args__ = (UniqueConstraint("session_id", "user_id", name="uq_session_membership"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(12), ForeignKey("course_sessions.id"))
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"))
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped["CourseSession"] = relationship(back_populates="memberships")
    user: Mapped["User"] = relationship()


class Gantt(Base):
    __tablename__ = "gantts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), default="Nouveau Gantt")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    session_id: Mapped[str | None] = mapped_column(String(12), ForeignKey("course_sessions.id"), nullable=True, default=None)

    tasks: Mapped[list["GanttTask"]] = relationship(
        back_populates="gantt", cascade="all, delete-orphan", order_by="GanttTask.order_index"
    )


class GanttTask(Base):
    __tablename__ = "gantt_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gantt_id: Mapped[str] = mapped_column(String(32), ForeignKey("gantts.id"))
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    indent_level: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(200), default="")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)

    gantt: Mapped["Gantt"] = relationship(back_populates="tasks")


class Whiteboard(Base):
    __tablename__ = "whiteboards"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), default="Nouveau tableau blanc")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    session_id: Mapped[str | None] = mapped_column(String(12), ForeignKey("course_sessions.id"), nullable=True, default=None)

    elements: Mapped[list["WhiteboardElement"]] = relationship(
        back_populates="whiteboard", cascade="all, delete-orphan"
    )


class KanbanBoard(Base):
    __tablename__ = "kanban_boards"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), default="Nouveau Kanban")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    session_id: Mapped[str | None] = mapped_column(String(12), ForeignKey("course_sessions.id"), nullable=True, default=None)

    columns: Mapped[list["KanbanColumn"]] = relationship(
        back_populates="board", cascade="all, delete-orphan", order_by="KanbanColumn.order_index"
    )


class KanbanColumn(Base):
    __tablename__ = "kanban_columns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    board_id: Mapped[str] = mapped_column(String(32), ForeignKey("kanban_boards.id"))
    title: Mapped[str] = mapped_column(String(200), default="")
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    board: Mapped["KanbanBoard"] = relationship(back_populates="columns")
    cards: Mapped[list["KanbanCard"]] = relationship(
        back_populates="column", cascade="all, delete-orphan", order_by="KanbanCard.order_index"
    )


class KanbanCard(Base):
    __tablename__ = "kanban_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    column_id: Mapped[int] = mapped_column(Integer, ForeignKey("kanban_columns.id"))
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(String(500), default="")
    color: Mapped[str] = mapped_column(String(7), default="#fff3bf")

    column: Mapped["KanbanColumn"] = relationship(back_populates="cards")


class CoursePresentation(Base):
    # one row per session: which course file the teacher is currently
    # presenting, where they currently are in it, and which slides have
    # already been shown (students may freely revisit any of those, but
    # not slides beyond the teacher's current position)
    __tablename__ = "course_presentations"

    session_id: Mapped[str] = mapped_column(String(12), ForeignKey("course_sessions.id"), primary_key=True)
    topic: Mapped[str | None] = mapped_column(String(200), nullable=True)
    current_slide_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # switching to a different topic resets this list — it only makes sense
    # relative to the topic it was recorded against
    visited_slide_ids: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class QcmSession(Base):
    # one row per "run" of a chapter's QCM within a course session — a
    # teacher can relaunch the same chapter's QCM later, which just creates
    # another row rather than overwriting the previous run's results
    __tablename__ = "qcm_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(12), ForeignKey("course_sessions.id"), index=True)
    topic: Mapped[str] = mapped_column(String(200))
    chapter_title: Mapped[str] = mapped_column(String(200))
    seconds_per_question: Mapped[int] = mapped_column(Integer, default=10)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # "pending" | "running" | "ended"
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class QcmAnswer(Base):
    __tablename__ = "qcm_answers"
    __table_args__ = (UniqueConstraint("qcm_session_id", "user_id", "question_index", name="uq_qcm_answer"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    qcm_session_id: Mapped[int] = mapped_column(Integer, ForeignKey("qcm_sessions.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"))
    question_index: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="not_seen")  # "not_seen" | "validated" | "deferred"
    selected_options: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool_type: Mapped[str] = mapped_column(String(20))  # "gantt" | "whiteboard" | "kanban"
    tool_id: Mapped[str] = mapped_column(String(32), index=True)
    # nullable: anonymous visitors (no account) can act on tools with no
    # session attached — actor_label still carries a human-readable "Anonyme"
    # in that case, this column is only kept for a possible future filter
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_label: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(20))  # "created" | "updated" | "deleted" | "renamed"
    summary: Mapped[str] = mapped_column(String(300))
    # optional full snapshot of whatever was acted on — currently only
    # populated for a whiteboard "deleted" entry, so it can be recreated
    # later ("undo"); nullable so every other action stays lightweight
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class WhiteboardElement(Base):
    __tablename__ = "whiteboard_elements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    whiteboard_id: Mapped[str] = mapped_column(String(32), ForeignKey("whiteboards.id"))
    type: Mapped[str] = mapped_column(String(20))
    x: Mapped[float] = mapped_column(Float, default=0)
    y: Mapped[float] = mapped_column(Float, default=0)
    width: Mapped[float] = mapped_column(Float, default=0)
    height: Mapped[float] = mapped_column(Float, default=0)
    z_index: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    # deliberately its own column, outside `data` — applies uniformly
    # across every element type, and keeping it out of ElementIn means a
    # plain content/position "update" op can never accidentally clobber it
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    whiteboard: Mapped["Whiteboard"] = relationship(back_populates="elements")
