import uuid

from fastapi.testclient import TestClient

from app.main import app


def _uname(prefix="user"):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _new_session_id(teacher_client):
    resp = teacher_client.post("/sessions/new", follow_redirects=False)
    assert resp.status_code == 303
    return resp.headers["location"].rsplit("/", 1)[-1]


def _new_gantt_id(client):
    resp = client.post("/gantt/new", follow_redirects=False)
    return resp.headers["location"].rsplit("/", 1)[-1]


def _new_kanban_id(client):
    resp = client.post("/kanban/new", follow_redirects=False)
    return resp.headers["location"].rsplit("/", 1)[-1]


def _join(student_client, session_id):
    resp = student_client.post(f"/join/{session_id}", follow_redirects=False)
    assert resp.status_code == 303


def test_unattached_tools_remain_open_to_anyone(client):
    # regression guard: creating tools without a session must keep behaving
    # exactly like before this feature existed
    gantt_id = _new_gantt_id(client)
    anon = TestClient(app)
    resp = anon.get(f"/gantt/{gantt_id}")
    assert resp.status_code == 200


def test_new_gantt_can_be_created_already_attached_to_a_session(teacher_client):
    session_id = _new_session_id(teacher_client)
    resp = teacher_client.post("/gantt/new", data={"session_id": session_id}, follow_redirects=False)
    assert resp.status_code == 303
    gantt_id = resp.headers["location"].rsplit("/", 1)[-1]

    detail = teacher_client.get(f"/sessions/{session_id}")
    assert gantt_id in detail.text


def test_attached_gantt_rejects_non_member_and_allows_member(client, teacher_client, student_client):
    session_id = _new_session_id(teacher_client)
    gantt_id = _new_gantt_id(teacher_client)

    teacher_client.post(
        f"/sessions/{session_id}/attach", data={"tool_type": "gantt", "tool_id": gantt_id}, follow_redirects=False
    )

    denied = client.get(f"/gantt/{gantt_id}", follow_redirects=False)
    assert denied.status_code == 303
    assert denied.headers["location"] == "/login"

    _join(student_client, session_id)
    allowed = student_client.get(f"/gantt/{gantt_id}")
    assert allowed.status_code == 200

    # the owning teacher also has access without being an explicit member
    owner_access = teacher_client.get(f"/gantt/{gantt_id}")
    assert owner_access.status_code == 200


def test_detach_restores_open_access(teacher_client, client):
    session_id = _new_session_id(teacher_client)
    gantt_id = _new_gantt_id(teacher_client)
    teacher_client.post(f"/sessions/{session_id}/attach", data={"tool_type": "gantt", "tool_id": gantt_id})

    still_blocked = client.get(f"/gantt/{gantt_id}", follow_redirects=False)
    assert still_blocked.status_code == 303

    teacher_client.post(f"/sessions/{session_id}/detach", data={"tool_type": "gantt", "tool_id": gantt_id})
    reopened = client.get(f"/gantt/{gantt_id}")
    assert reopened.status_code == 200


def test_attached_kanban_ws_rejects_non_member(teacher_client, client):
    session_id = _new_session_id(teacher_client)
    kanban_id = _new_kanban_id(teacher_client)
    teacher_client.post(f"/sessions/{session_id}/attach", data={"tool_type": "kanban", "tool_id": kanban_id})

    from starlette.websockets import WebSocketDisconnect
    import pytest

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/kanban/{kanban_id}"):
            pass


def test_attached_kanban_ws_allows_member(teacher_client, student_client):
    session_id = _new_session_id(teacher_client)
    kanban_id = _new_kanban_id(teacher_client)
    teacher_client.post(f"/sessions/{session_id}/attach", data={"tool_type": "kanban", "tool_id": kanban_id})
    _join(student_client, session_id)

    with student_client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "create_column", "title": "x"})
        msg = ws.receive_json()
    assert msg["op"] == "column_created"


def test_blocked_session_denies_access_even_to_members(teacher_client, student_client):
    session_id = _new_session_id(teacher_client)
    gantt_id = _new_gantt_id(teacher_client)
    teacher_client.post(f"/sessions/{session_id}/attach", data={"tool_type": "gantt", "tool_id": gantt_id})
    _join(student_client, session_id)

    assert student_client.get(f"/gantt/{gantt_id}").status_code == 200

    teacher_client.post(f"/sessions/{session_id}/toggle-active")  # now blocked
    resp = student_client.get(f"/gantt/{gantt_id}", follow_redirects=False)
    assert resp.status_code == 403

    # the owning teacher can still manage it regardless of the block
    assert teacher_client.get(f"/gantt/{gantt_id}").status_code == 200


def test_admin_always_has_access_to_session_scoped_tools(teacher_client, admin_client):
    session_id = _new_session_id(teacher_client)
    gantt_id = _new_gantt_id(teacher_client)
    teacher_client.post(f"/sessions/{session_id}/attach", data={"tool_type": "gantt", "tool_id": gantt_id})

    resp = admin_client.get(f"/gantt/{gantt_id}")
    assert resp.status_code == 200


def test_attached_whiteboard_api_and_page_rejects_non_member(teacher_client, client):
    session_id = _new_session_id(teacher_client)
    detail = teacher_client.get(f"/sessions/{session_id}")
    # the whiteboard auto-created with the session
    import re

    match = re.search(r"/whiteboard/([0-9a-f]{32})", detail.text)
    assert match
    whiteboard_id = match.group(1)

    page = client.get(f"/whiteboard/{whiteboard_id}", follow_redirects=False)
    assert page.status_code == 303
    api = client.get(f"/api/whiteboard/{whiteboard_id}")
    assert api.status_code == 403


def test_attached_gantt_history_follows_the_same_access_rules(client, teacher_client, student_client):
    # the history endpoint must be gated exactly like the rest of a
    # session-scoped tool's API — no separate, looser check for it
    session_id = _new_session_id(teacher_client)
    gantt_id = _new_gantt_id(teacher_client)
    teacher_client.post(
        f"/sessions/{session_id}/attach", data={"tool_type": "gantt", "tool_id": gantt_id}, follow_redirects=False
    )

    assert client.get(f"/api/gantt/{gantt_id}/history").status_code == 403

    _join(student_client, session_id)
    assert student_client.get(f"/api/gantt/{gantt_id}/history").status_code == 200
    assert teacher_client.get(f"/api/gantt/{gantt_id}/history").status_code == 200
