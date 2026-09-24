import pytest
from starlette.websockets import WebSocketDisconnect


def _new_session_id(teacher_client):
    resp = teacher_client.post("/sessions/new", follow_redirects=False)
    assert resp.status_code == 303
    return resp.headers["location"].rsplit("/", 1)[-1]


def _join(student_client, session_id):
    resp = student_client.post(f"/join/{session_id}", follow_redirects=False)
    assert resp.status_code == 303


@pytest.mark.parametrize(
    "bad_id",
    ["not-a-valid-id", "a" * 6, "a" * 8, "'; DROP TABLE course_sessions; --"],
)
def test_malformed_session_id_rejected_on_cours_routes(client, bad_id):
    assert client.get(f"/sessions/{bad_id}/cours").status_code == 422
    assert client.get(f"/api/cours/{bad_id}/content").status_code == 422


def test_websocket_rejects_malformed_session_id(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/cours/not-a-valid-id"):
            pass


def test_unknown_session_id_404(teacher_client):
    well_formed_but_unknown = "ZZZZZZZ"
    assert teacher_client.get(f"/sessions/{well_formed_but_unknown}/cours").status_code == 404
    assert teacher_client.get(f"/api/cours/{well_formed_but_unknown}/content").status_code == 404


def test_anonymous_cannot_view_cours_page(client, teacher_client):
    session_id = _new_session_id(teacher_client)
    resp = client.get(f"/sessions/{session_id}/cours", follow_redirects=False)
    assert resp.status_code in (303, 403)


def test_non_member_student_denied(client, teacher_client, student_client):
    # student_client exists but never joined this particular session
    session_id = _new_session_id(teacher_client)
    resp = student_client.get(f"/sessions/{session_id}/cours", follow_redirects=False)
    assert resp.status_code == 403


def test_websocket_denies_non_member(teacher_client, student_client):
    session_id = _new_session_id(teacher_client)
    with pytest.raises(WebSocketDisconnect):
        with student_client.websocket_connect(f"/ws/cours/{session_id}"):
            pass


def test_blocked_session_denies_member_access(teacher_client, student_client):
    session_id = _new_session_id(teacher_client)
    _join(student_client, session_id)
    assert student_client.get(f"/sessions/{session_id}/cours").status_code == 200

    teacher_client.post(f"/sessions/{session_id}/toggle-active")  # now blocked
    resp = student_client.get(f"/sessions/{session_id}/cours", follow_redirects=False)
    assert resp.status_code == 403

    # the owning teacher can still access it regardless of the block
    assert teacher_client.get(f"/sessions/{session_id}/cours").status_code == 200


def test_select_topic_cannot_escape_courses_dir(teacher_client):
    session_id = _new_session_id(teacher_client)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        for payload in ["../../requirements.txt", "/etc/passwd", "001_test.md/../../secret.md"]:
            ws.send_json({"op": "select_topic", "topic": payload})
            assert ws.receive_json()["op"] == "error"


def test_select_topic_rejects_qcm_file_even_with_exact_name(teacher_client):
    session_id = _new_session_id(teacher_client)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        ws.send_json({"op": "select_topic", "topic": "001_test_qcm.md"})
        assert ws.receive_json()["op"] == "error"


def test_websocket_rejects_malformed_json(teacher_client):
    session_id = teacher_client.post("/sessions/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        ws.send_text("not json")
        assert ws.receive_json()["op"] == "error"


def test_admin_always_has_access(teacher_client, admin_client):
    session_id = _new_session_id(teacher_client)
    resp = admin_client.get(f"/sessions/{session_id}/cours")
    assert resp.status_code == 200
