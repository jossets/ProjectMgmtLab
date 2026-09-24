from fastapi.testclient import TestClient

from app.main import app


def _new_session_id(teacher_client):
    resp = teacher_client.post("/sessions/new", follow_redirects=False)
    assert resp.status_code == 303
    return resp.headers["location"].rsplit("/", 1)[-1]


def _join(student_client, session_id):
    resp = student_client.post(f"/join/{session_id}", follow_redirects=False)
    assert resp.status_code == 303


def test_topics_list_excludes_qcm_and_non_matching_files():
    from app import courses

    topics = courses.list_topics()
    filenames = [t["filename"] for t in topics]
    assert "001_test.md" in filenames
    assert "002_autre.md" in filenames
    assert "001_test_qcm.md" not in filenames
    assert "thématiques.md" not in filenames


def test_owner_sees_topic_picker_before_any_topic_selected(teacher_client):
    session_id = _new_session_id(teacher_client)
    resp = teacher_client.get(f"/sessions/{session_id}/cours")
    assert resp.status_code == 200
    assert "Choisir un cours" in resp.text or "cours-picker" in resp.text


def test_teacher_selects_topic_and_it_persists(teacher_client):
    session_id = _new_session_id(teacher_client)

    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        ws.send_json({"op": "select_topic", "topic": "001_test.md"})
        msg = ws.receive_json()
        assert msg["op"] == "topic_changed"
        assert msg["topic"] == "001_test.md"
        assert msg["current_slide_id"] == 0
        assert msg["visited_slide_ids"] == [0]

    content = teacher_client.get(f"/api/cours/{session_id}/content")
    assert content.status_code == 200
    data = content.json()
    assert data["topic"] == "001_test.md"
    assert data["title"] == "Cours de test"
    assert len(data["tree"]) == 2  # two top-level chapters
    assert data["slides"]["0"]["title"] == "Chapitre un"

    # reloading the page reflects the persisted state
    page = teacher_client.get(f"/sessions/{session_id}/cours")
    assert page.status_code == 200
    assert "Cours de test" in page.text


def test_teacher_goto_updates_current_slide_and_visited_set(teacher_client):
    session_id = _new_session_id(teacher_client)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        ws.send_json({"op": "select_topic", "topic": "001_test.md"})
        ws.receive_json()
        ws.send_json({"op": "goto", "slide_id": 2})
        msg = ws.receive_json()
        assert msg["op"] == "slide_changed"
        assert msg["current_slide_id"] == 2
        assert sorted(msg["visited_slide_ids"]) == [0, 2]


def test_goto_broadcasts_to_other_connected_clients(teacher_client, student_client):
    session_id = _new_session_id(teacher_client)
    _join(student_client, session_id)

    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as teacher_ws:
        teacher_ws.send_json({"op": "select_topic", "topic": "001_test.md"})
        teacher_ws.receive_json()

        with student_client.websocket_connect(f"/ws/cours/{session_id}") as student_ws:
            teacher_ws.send_json({"op": "goto", "slide_id": 3})
            teacher_ws.receive_json()
            student_msg = student_ws.receive_json()
            assert student_msg["op"] == "slide_changed"
            assert student_msg["current_slide_id"] == 3


def test_student_cannot_drive_the_presentation(teacher_client, student_client):
    session_id = _new_session_id(teacher_client)
    _join(student_client, session_id)

    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as teacher_ws:
        teacher_ws.send_json({"op": "select_topic", "topic": "001_test.md"})
        teacher_ws.receive_json()

    with student_client.websocket_connect(f"/ws/cours/{session_id}") as student_ws:
        student_ws.send_json({"op": "goto", "slide_id": 2})
        msg = student_ws.receive_json()
        assert msg["op"] == "error"

    # the teacher's position was not affected by the rejected student op
    content = teacher_client.get(f"/api/cours/{session_id}/content").json()
    assert content["current_slide_id"] == 0


def test_switching_topic_resets_visited_slides(teacher_client):
    session_id = _new_session_id(teacher_client)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        ws.send_json({"op": "select_topic", "topic": "001_test.md"})
        ws.receive_json()
        ws.send_json({"op": "goto", "slide_id": 3})
        ws.receive_json()

        ws.send_json({"op": "select_topic", "topic": "002_autre.md"})
        msg = ws.receive_json()
        assert msg["topic"] == "002_autre.md"
        assert msg["visited_slide_ids"] == [0]


def test_select_unknown_or_qcm_topic_rejected(teacher_client):
    session_id = _new_session_id(teacher_client)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        ws.send_json({"op": "select_topic", "topic": "001_test_qcm.md"})
        assert ws.receive_json()["op"] == "error"
        ws.send_json({"op": "select_topic", "topic": "does_not_exist.md"})
        assert ws.receive_json()["op"] == "error"


def test_goto_unknown_slide_id_rejected(teacher_client):
    session_id = _new_session_id(teacher_client)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        ws.send_json({"op": "select_topic", "topic": "001_test.md"})
        ws.receive_json()
        ws.send_json({"op": "goto", "slide_id": 999})
        assert ws.receive_json()["op"] == "error"


def test_goto_before_any_topic_selected_rejected(teacher_client):
    session_id = _new_session_id(teacher_client)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        ws.send_json({"op": "goto", "slide_id": 0})
        assert ws.receive_json()["op"] == "error"


def test_content_api_404_before_any_topic_selected(teacher_client):
    session_id = _new_session_id(teacher_client)
    resp = teacher_client.get(f"/api/cours/{session_id}/content")
    assert resp.status_code == 404


def test_student_sees_waiting_state_before_topic_selected(teacher_client, student_client):
    session_id = _new_session_id(teacher_client)
    _join(student_client, session_id)
    resp = student_client.get(f"/sessions/{session_id}/cours")
    assert resp.status_code == 200
    assert "attente" in resp.text


def test_websocket_does_not_hold_a_db_connection_while_idle(teacher_client):
    # regression guard — see the matching test in test_functional_whiteboard.py.
    # cours_ws is the most DB-heavy of the three (also checks/broadcasts
    # QCM state on connect and disconnect), so it's worth its own check.
    from app.db import engine

    # (unlike whiteboard/kanban, connecting here also does a QCM-state DB
    # check right after accept()ing — which itself races the test client's
    # own accept()-triggered unblock — so only the *settled*, post-round-trip
    # state is meaningful here, not the instant right after connecting)
    session_id = _new_session_id(teacher_client)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        ws.send_json({"op": "select_topic", "topic": "001_test.md"})
        ws.receive_json()
        assert engine.pool.checkedout() == 0
