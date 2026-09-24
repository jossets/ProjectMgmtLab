import pytest


def _new_session_id(teacher_client):
    resp = teacher_client.post("/sessions/new", follow_redirects=False)
    assert resp.status_code == 303
    return resp.headers["location"].rsplit("/", 1)[-1]


def _join(student_client, session_id):
    resp = student_client.post(f"/join/{session_id}", follow_redirects=False)
    assert resp.status_code == 303


def _select_and_prepare(ws, chapter_title="Chapitre un", topic="001_test.md"):
    ws.send_json({"op": "select_topic", "topic": topic})
    ws.receive_json()
    ws.send_json({"op": "qcm_prepare", "chapter_title": chapter_title})
    return ws.receive_json()


@pytest.mark.parametrize("bad_qcm_id", ["not-an-int", "-1", "0", "1.5"])
def test_malformed_qcm_session_id_rejected(teacher_client, bad_qcm_id):
    session_id = _new_session_id(teacher_client)
    resp = teacher_client.get(f"/sessions/{session_id}/qcm/{bad_qcm_id}/results")
    assert resp.status_code == 422


def test_unknown_qcm_session_id_404(teacher_client):
    session_id = _new_session_id(teacher_client)
    resp = teacher_client.get(f"/sessions/{session_id}/qcm/999999/results")
    assert resp.status_code == 404


def test_qcm_from_another_session_not_reachable(teacher_client):
    session_a = _new_session_id(teacher_client)
    session_b = _new_session_id(teacher_client)

    with teacher_client.websocket_connect(f"/ws/cours/{session_a}") as ws:
        msg = _select_and_prepare(ws)
        assert msg["op"] == "qcm_state"
        qcm_session_id = msg["qcm_session_id"]

    # the same qcm_session_id must not resolve under a *different* session
    resp = teacher_client.get(f"/sessions/{session_b}/qcm/{qcm_session_id}/results")
    assert resp.status_code == 404


def test_anonymous_cannot_view_results(client, teacher_client):
    session_id = _new_session_id(teacher_client)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        msg = _select_and_prepare(ws)
        qcm_session_id = msg["qcm_session_id"]

    resp = client.get(f"/sessions/{session_id}/qcm/{qcm_session_id}/results", follow_redirects=False)
    assert resp.status_code in (303, 403)


def test_member_student_cannot_view_results(teacher_client, student_client):
    session_id = _new_session_id(teacher_client)
    _join(student_client, session_id)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        msg = _select_and_prepare(ws)
        qcm_session_id = msg["qcm_session_id"]

    resp = student_client.get(f"/sessions/{session_id}/qcm/{qcm_session_id}/results", follow_redirects=False)
    assert resp.status_code == 403


def test_negative_option_index_rejected(teacher_client, student_client):
    session_id = _new_session_id(teacher_client)
    _join(student_client, session_id)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as teacher_ws:
        _select_and_prepare(teacher_ws)
        teacher_ws.send_json({"op": "qcm_start"})
        teacher_ws.receive_json()

        with student_client.websocket_connect(f"/ws/cours/{session_id}") as student_ws:
            teacher_ws.receive_json()
            student_ws.receive_json()
            student_ws.send_json({"op": "qcm_answer", "question_index": 0, "option_indices": [-1], "action": "validate"})
            assert student_ws.receive_json()["op"] == "error"


def test_seconds_per_question_out_of_bounds_rejected(teacher_client):
    session_id = _new_session_id(teacher_client)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        ws.send_json({"op": "select_topic", "topic": "001_test.md"})
        ws.receive_json()
        ws.send_json({"op": "qcm_prepare", "chapter_title": "Chapitre un", "seconds_per_question": 1})
        assert ws.receive_json()["op"] == "error"
        ws.send_json({"op": "qcm_prepare", "chapter_title": "Chapitre un", "seconds_per_question": 99999})
        assert ws.receive_json()["op"] == "error"


def test_overlong_chapter_title_rejected(teacher_client):
    session_id = _new_session_id(teacher_client)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        ws.send_json({"op": "select_topic", "topic": "001_test.md"})
        ws.receive_json()
        ws.send_json({"op": "qcm_prepare", "chapter_title": "x" * 300})
        assert ws.receive_json()["op"] == "error"


def test_admin_can_view_results(teacher_client, admin_client):
    session_id = _new_session_id(teacher_client)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        msg = _select_and_prepare(ws)
        qcm_session_id = msg["qcm_session_id"]

    resp = admin_client.get(f"/sessions/{session_id}/qcm/{qcm_session_id}/results")
    assert resp.status_code == 200
