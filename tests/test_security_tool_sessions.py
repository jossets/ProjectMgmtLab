import uuid

import pytest
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


def _other_teacher_client(admin_client):
    username = _uname("otherteacher")
    admin_client.post("/accounts/teachers", data={"username": username, "password": "Sup3rSecret!"}, follow_redirects=False)
    c = TestClient(app)
    resp = c.post("/login", data={"username": username, "password": "Sup3rSecret!"}, follow_redirects=False)
    assert resp.status_code == 303
    return c


def test_create_tool_with_session_owned_by_another_teacher_rejected(admin_client, teacher_client):
    session_id = _new_session_id(teacher_client)
    intruder = _other_teacher_client(admin_client)

    resp = intruder.post("/gantt/new", data={"session_id": session_id})
    assert resp.status_code == 403


@pytest.mark.parametrize("bad_session_id", ["short", "toolongforthis", "abcdefg", "3d6gh75"])
def test_create_tool_rejects_malformed_session_id(teacher_client, bad_session_id):
    resp = teacher_client.post("/gantt/new", data={"session_id": bad_session_id})
    assert resp.status_code == 422


def test_create_tool_rejects_unknown_session_id(teacher_client):
    resp = teacher_client.post("/gantt/new", data={"session_id": "ZZZZZZZ"})
    assert resp.status_code == 404


def test_attach_requires_ownership(admin_client, teacher_client):
    session_id = _new_session_id(teacher_client)
    gantt_id = _new_gantt_id(teacher_client)
    intruder = _other_teacher_client(admin_client)

    resp = intruder.post(f"/sessions/{session_id}/attach", data={"tool_type": "gantt", "tool_id": gantt_id})
    assert resp.status_code == 403


def test_attach_rejects_tool_already_owned_by_another_teachers_session(admin_client, teacher_client):
    # the core IDOR this guards against: knowing another teacher's board id
    # must never be enough to steal it into your own session — that would
    # both grant the attacker full owner access to it and silently cut the
    # original session off from it
    victim_session_id = _new_session_id(teacher_client)
    gantt_id = _new_gantt_id(teacher_client)
    teacher_client.post(f"/sessions/{victim_session_id}/attach", data={"tool_type": "gantt", "tool_id": gantt_id})

    intruder = _other_teacher_client(admin_client)
    intruder_session_id = _new_session_id(intruder)

    resp = intruder.post(
        f"/sessions/{intruder_session_id}/attach", data={"tool_type": "gantt", "tool_id": gantt_id}
    )
    assert resp.status_code == 403

    # still attached to the victim's session, untouched
    detail = teacher_client.get(f"/sessions/{victim_session_id}")
    assert gantt_id in detail.text
    assert intruder.get(f"/api/gantt/{gantt_id}").status_code == 403


def test_attach_allows_an_orphan_tool_with_no_session(teacher_client):
    # a tool that was never attached anywhere is fair game, same "open to
    # anyone with the link" rule as check_board_access
    session_id = _new_session_id(teacher_client)
    gantt_id = _new_gantt_id(teacher_client)

    resp = teacher_client.post(
        f"/sessions/{session_id}/attach", data={"tool_type": "gantt", "tool_id": gantt_id}, follow_redirects=False
    )
    assert resp.status_code == 303
    detail = teacher_client.get(f"/sessions/{session_id}")
    assert gantt_id in detail.text


def test_attach_allows_reattaching_a_tool_already_owned_by_the_same_teacher(teacher_client):
    session_a = _new_session_id(teacher_client)
    session_b = _new_session_id(teacher_client)
    gantt_id = _new_gantt_id(teacher_client)
    teacher_client.post(f"/sessions/{session_a}/attach", data={"tool_type": "gantt", "tool_id": gantt_id})

    resp = teacher_client.post(
        f"/sessions/{session_b}/attach", data={"tool_type": "gantt", "tool_id": gantt_id}, follow_redirects=False
    )
    assert resp.status_code == 303
    detail = teacher_client.get(f"/sessions/{session_b}")
    assert gantt_id in detail.text


def test_admin_can_attach_a_tool_owned_by_any_teachers_session(admin_client, teacher_client):
    victim_session_id = _new_session_id(teacher_client)
    gantt_id = _new_gantt_id(teacher_client)
    teacher_client.post(f"/sessions/{victim_session_id}/attach", data={"tool_type": "gantt", "tool_id": gantt_id})

    admin_session_id = _new_session_id(admin_client)
    resp = admin_client.post(
        f"/sessions/{admin_session_id}/attach", data={"tool_type": "gantt", "tool_id": gantt_id}, follow_redirects=False
    )
    assert resp.status_code == 303


@pytest.mark.parametrize("bad_type", ["invalid", "GANTT", "gantt;drop", ""])
def test_attach_rejects_malformed_tool_type(teacher_client, bad_type):
    session_id = _new_session_id(teacher_client)
    gantt_id = _new_gantt_id(teacher_client)
    resp = teacher_client.post(
        f"/sessions/{session_id}/attach", data={"tool_type": bad_type, "tool_id": gantt_id}
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("bad_id", ["not-a-valid-id", "a" * 31, "a" * 33, "A" * 32])
def test_attach_rejects_malformed_tool_id(teacher_client, bad_id):
    session_id = _new_session_id(teacher_client)
    resp = teacher_client.post(
        f"/sessions/{session_id}/attach", data={"tool_type": "gantt", "tool_id": bad_id}
    )
    assert resp.status_code == 422


def test_attach_rejects_nonexistent_tool(teacher_client):
    session_id = _new_session_id(teacher_client)
    resp = teacher_client.post(
        f"/sessions/{session_id}/attach", data={"tool_type": "gantt", "tool_id": "a" * 32}
    )
    assert resp.status_code == 404


def test_detach_rejects_tool_attached_to_a_different_session(teacher_client):
    session_a = _new_session_id(teacher_client)
    session_b = _new_session_id(teacher_client)
    gantt_id = _new_gantt_id(teacher_client)
    teacher_client.post(f"/sessions/{session_a}/attach", data={"tool_type": "gantt", "tool_id": gantt_id})

    resp = teacher_client.post(
        f"/sessions/{session_b}/detach", data={"tool_type": "gantt", "tool_id": gantt_id}
    )
    assert resp.status_code == 404

    # still attached to session_a, untouched
    detail = teacher_client.get(f"/sessions/{session_a}")
    assert gantt_id in detail.text


def test_detach_requires_ownership(admin_client, teacher_client):
    session_id = _new_session_id(teacher_client)
    gantt_id = _new_gantt_id(teacher_client)
    teacher_client.post(f"/sessions/{session_id}/attach", data={"tool_type": "gantt", "tool_id": gantt_id})
    intruder = _other_teacher_client(admin_client)

    resp = intruder.post(f"/sessions/{session_id}/detach", data={"tool_type": "gantt", "tool_id": gantt_id})
    assert resp.status_code == 403


def test_attached_whiteboard_upload_image_rejects_non_member(teacher_client, client, png_bytes):
    session_id = _new_session_id(teacher_client)
    import re

    detail = teacher_client.get(f"/sessions/{session_id}")
    match = re.search(r"/whiteboard/([0-9a-f]{32})", detail.text)
    whiteboard_id = match.group(1)

    resp = client.post(
        f"/api/whiteboard/{whiteboard_id}/upload-image",
        files={"file": ("photo.png", png_bytes, "image/png")},
    )
    assert resp.status_code == 403


def test_attached_whiteboard_ws_rejects_non_member(teacher_client, client):
    session_id = _new_session_id(teacher_client)
    import re

    from starlette.websockets import WebSocketDisconnect

    detail = teacher_client.get(f"/sessions/{session_id}")
    match = re.search(r"/whiteboard/([0-9a-f]{32})", detail.text)
    whiteboard_id = match.group(1)

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}"):
            pass


def test_attached_gantt_api_rejects_non_member(teacher_client, client):
    session_id = _new_session_id(teacher_client)
    gantt_id = _new_gantt_id(teacher_client)
    teacher_client.post(f"/sessions/{session_id}/attach", data={"tool_type": "gantt", "tool_id": gantt_id})

    assert client.get(f"/api/gantt/{gantt_id}").status_code == 403
    assert client.put(f"/api/gantt/{gantt_id}/tasks", json=[]).status_code == 403
    assert client.patch(f"/api/gantt/{gantt_id}", json={"name": "hacked"}).status_code == 403
