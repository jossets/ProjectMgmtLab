import time

# in-memory, single-process — same accepted tradeoff as the login
# rate-limiter (app/routers/auth.py): resets on restart, not shared across
# workers. Fine for this app's documented single-worker deployment.
_PRESENCE: dict[str, dict] = {}  # user_id -> {"page": str, "visible": bool, "updated_at": float}

STALE_AFTER_SECONDS = 30


def record_ping(user_id: str, page: str, visible: bool) -> None:
    _PRESENCE[user_id] = {"page": page, "visible": visible, "updated_at": time.monotonic()}


def record_gone(user_id: str) -> None:
    _PRESENCE.pop(user_id, None)


def get_presence(user_id: str) -> dict | None:
    entry = _PRESENCE.get(user_id)
    if entry is None:
        return None
    if time.monotonic() - entry["updated_at"] > STALE_AFTER_SECONDS:
        _PRESENCE.pop(user_id, None)
        return None
    return entry
