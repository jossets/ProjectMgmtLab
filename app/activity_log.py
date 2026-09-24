from sqlalchemy.orm import Session

from app.auth import Identity
from app.models import ActivityLog

MAX_SUMMARY_LENGTH = 300
MAX_HISTORY_ENTRIES = 200


def actor_label(identity: Identity) -> str:
    if identity.is_admin:
        return "Admin"
    if identity.user is not None:
        return identity.user.username
    return "Anonyme"


def log_activity(
    db: Session,
    tool_type: str,
    tool_id: str,
    identity: Identity,
    action: str,
    summary: str,
    payload: dict | None = None,
) -> None:
    # caller is responsible for committing — keeps the log entry in the
    # same transaction as the change it describes, so one never exists
    # without the other
    db.add(
        ActivityLog(
            tool_type=tool_type,
            tool_id=tool_id,
            actor_id=identity.id,
            actor_label=actor_label(identity),
            action=action,
            summary=summary[:MAX_SUMMARY_LENGTH],
            payload=payload,
        )
    )


def serialize_entry(entry: ActivityLog) -> dict:
    return {
        "id": entry.id,
        "actor_label": entry.actor_label,
        "action": entry.action,
        "summary": entry.summary,
        "created_at": entry.created_at.isoformat(),
        # the raw payload never leaves the server via the history listing —
        # only whether a restore is possible; the client sends the entry's
        # id back and the server re-reads the payload itself
        "can_restore": entry.action == "deleted" and entry.payload is not None,
    }


def get_history(db: Session, tool_type: str, tool_id: str, limit: int = MAX_HISTORY_ENTRIES) -> list[dict]:
    entries = (
        db.query(ActivityLog)
        .filter(ActivityLog.tool_type == tool_type, ActivityLog.tool_id == tool_id)
        .order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
        .limit(limit)
        .all()
    )
    return [serialize_entry(e) for e in entries]
