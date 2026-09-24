from datetime import datetime, timedelta

from app.db import SessionLocal
from app.models import QcmSession


def _new_session_id(teacher_client):
    resp = teacher_client.post("/sessions/new", follow_redirects=False)
    assert resp.status_code == 303
    return resp.headers["location"].rsplit("/", 1)[-1]


def _join(student_client, session_id):
    resp = student_client.post(f"/join/{session_id}", follow_redirects=False)
    assert resp.status_code == 303


def _select_topic(ws, topic="001_test.md"):
    ws.send_json({"op": "select_topic", "topic": topic})
    return ws.receive_json()


def test_qcm_available_reported_per_chapter(teacher_client):
    session_id = _new_session_id(teacher_client)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        _select_topic(ws)
    content = teacher_client.get(f"/api/cours/{session_id}/content").json()
    assert content["qcm_available"] == {"Chapitre un": 2}


def test_prepare_and_start_qcm(teacher_client):
    session_id = _new_session_id(teacher_client)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        _select_topic(ws)

        ws.send_json({"op": "qcm_prepare", "chapter_title": "Chapitre un", "seconds_per_question": 10})
        msg = ws.receive_json()
        assert msg["op"] == "qcm_state"
        assert msg["status"] == "pending"
        assert msg["question_count"] == 2
        assert msg["roster"] == []  # no students joined yet

        ws.send_json({"op": "qcm_start"})
        msg = ws.receive_json()
        assert msg["status"] == "running"
        assert msg["ends_at"] is not None


def test_prepare_rejects_chapter_without_qcm(teacher_client):
    session_id = _new_session_id(teacher_client)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        _select_topic(ws, "001_test.md")
        ws.send_json({"op": "qcm_prepare", "chapter_title": "Chapitre deux", "seconds_per_question": 10})
        assert ws.receive_json()["op"] == "error"


def test_cannot_prepare_second_qcm_while_one_active(teacher_client):
    session_id = _new_session_id(teacher_client)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        _select_topic(ws)
        ws.send_json({"op": "qcm_prepare", "chapter_title": "Chapitre un"})
        ws.receive_json()
        ws.send_json({"op": "qcm_prepare", "chapter_title": "Chapitre un"})
        assert ws.receive_json()["op"] == "error"


def test_student_validate_and_defer_answers(teacher_client, student_client):
    session_id = _new_session_id(teacher_client)
    _join(student_client, session_id)

    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as teacher_ws:
        _select_topic(teacher_ws)
        teacher_ws.send_json({"op": "qcm_prepare", "chapter_title": "Chapitre un"})
        teacher_ws.receive_json()
        teacher_ws.send_json({"op": "qcm_start"})
        teacher_ws.receive_json()

        with student_client.websocket_connect(f"/ws/cours/{session_id}") as student_ws:
            # connecting broadcast to the teacher (roster refresh) + to the student (own state)
            teacher_ws.receive_json()
            student_msg = student_ws.receive_json()
            assert student_msg["op"] == "qcm_state"
            assert "questions" in student_msg
            assert all("correct" not in o for q in student_msg["questions"] for o in q["options"])

            student_ws.send_json({"op": "qcm_answer", "question_index": 0, "option_indices": [0], "action": "validate"})
            progress = student_ws.receive_json()
            assert progress["op"] == "qcm_progress"
            assert progress["status"] == "validated"

            teacher_progress = teacher_ws.receive_json()
            assert teacher_progress["op"] == "qcm_progress"
            assert teacher_progress["question_index"] == 0

            student_ws.send_json({"op": "qcm_answer", "question_index": 1, "option_indices": [], "action": "defer"})
            defer_msg = student_ws.receive_json()
            assert defer_msg["status"] == "deferred"
            teacher_ws.receive_json()


def test_owner_cannot_answer_and_student_cannot_drive(teacher_client, student_client):
    session_id = _new_session_id(teacher_client)
    _join(student_client, session_id)

    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as teacher_ws:
        _select_topic(teacher_ws)
        teacher_ws.send_json({"op": "qcm_prepare", "chapter_title": "Chapitre un"})
        teacher_ws.receive_json()
        teacher_ws.send_json({"op": "qcm_start"})
        teacher_ws.receive_json()

        teacher_ws.send_json({"op": "qcm_answer", "question_index": 0, "option_indices": [0], "action": "validate"})
        assert teacher_ws.receive_json()["op"] == "error"

    with student_client.websocket_connect(f"/ws/cours/{session_id}") as student_ws:
        student_ws.receive_json()  # own qcm_state on connect
        student_ws.send_json({"op": "qcm_prepare", "chapter_title": "Chapitre un"})
        assert student_ws.receive_json()["op"] == "error"
        student_ws.send_json({"op": "qcm_start"})
        assert student_ws.receive_json()["op"] == "error"
        student_ws.send_json({"op": "qcm_stop"})
        assert student_ws.receive_json()["op"] == "error"


def test_invalid_answer_payload_rejected(teacher_client, student_client):
    session_id = _new_session_id(teacher_client)
    _join(student_client, session_id)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as teacher_ws:
        _select_topic(teacher_ws)
        teacher_ws.send_json({"op": "qcm_prepare", "chapter_title": "Chapitre un"})
        teacher_ws.receive_json()
        teacher_ws.send_json({"op": "qcm_start"})
        teacher_ws.receive_json()

        with student_client.websocket_connect(f"/ws/cours/{session_id}") as student_ws:
            teacher_ws.receive_json()
            student_ws.receive_json()

            student_ws.send_json({"op": "qcm_answer", "question_index": 99, "option_indices": [0], "action": "validate"})
            assert student_ws.receive_json()["op"] == "error"

            student_ws.send_json({"op": "qcm_answer", "question_index": 0, "option_indices": [99], "action": "validate"})
            assert student_ws.receive_json()["op"] == "error"


def test_stop_ends_qcm_and_blocks_further_answers(teacher_client, student_client):
    session_id = _new_session_id(teacher_client)
    _join(student_client, session_id)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as teacher_ws:
        _select_topic(teacher_ws)
        teacher_ws.send_json({"op": "qcm_prepare", "chapter_title": "Chapitre un"})
        teacher_ws.receive_json()
        teacher_ws.send_json({"op": "qcm_start"})
        teacher_ws.receive_json()

        with student_client.websocket_connect(f"/ws/cours/{session_id}") as student_ws:
            teacher_ws.receive_json()
            student_ws.receive_json()

            teacher_ws.send_json({"op": "qcm_stop"})
            teacher_end = teacher_ws.receive_json()
            assert teacher_end["status"] == "ended"
            student_end = student_ws.receive_json()
            assert student_end["status"] == "ended"

            student_ws.send_json({"op": "qcm_answer", "question_index": 0, "option_indices": [0], "action": "validate"})
            assert student_ws.receive_json()["op"] == "error"


def test_expired_qcm_is_swept_and_rejects_answers(teacher_client, student_client):
    session_id = _new_session_id(teacher_client)
    _join(student_client, session_id)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as teacher_ws:
        _select_topic(teacher_ws)
        teacher_ws.send_json({"op": "qcm_prepare", "chapter_title": "Chapitre un"})
        teacher_ws.receive_json()
        teacher_ws.send_json({"op": "qcm_start"})
        teacher_ws.receive_json()

    # force the deadline into the past, simulating time running out
    db = SessionLocal()
    try:
        qs = db.query(QcmSession).filter(QcmSession.session_id == session_id).order_by(QcmSession.id.desc()).first()
        qs.ends_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()

    with student_client.websocket_connect(f"/ws/cours/{session_id}") as student_ws:
        state = student_ws.receive_json()
        assert state["status"] == "ended"
        student_ws.send_json({"op": "qcm_answer", "question_index": 0, "option_indices": [0], "action": "validate"})
        assert student_ws.receive_json()["op"] == "error"


def test_results_page_shows_counts_and_scores(teacher_client, student_client):
    session_id = _new_session_id(teacher_client)
    _join(student_client, session_id)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as teacher_ws:
        _select_topic(teacher_ws)
        teacher_ws.send_json({"op": "qcm_prepare", "chapter_title": "Chapitre un"})
        teacher_ws.receive_json()
        teacher_ws.send_json({"op": "qcm_start"})
        teacher_ws.receive_json()

        with student_client.websocket_connect(f"/ws/cours/{session_id}") as student_ws:
            teacher_ws.receive_json()
            student_ws.receive_json()
            # Q1 correct answer is option 0 ("oui")
            student_ws.send_json({"op": "qcm_answer", "question_index": 0, "option_indices": [0], "action": "validate"})
            student_ws.receive_json()
            teacher_ws.receive_json()

        teacher_ws.send_json({"op": "qcm_stop"})
        teacher_ws.receive_json()

    db = SessionLocal()
    try:
        qs = db.query(QcmSession).filter(QcmSession.session_id == session_id).order_by(QcmSession.id.desc()).first()
        qid = qs.id
    finally:
        db.close()

    resp = teacher_client.get(f"/sessions/{session_id}/qcm/{qid}/results")
    assert resp.status_code == 200
    assert "1 réponse" in resp.text  # one student picked "oui"
    assert "1 / 2" in resp.text  # 1 question answered out of 2, and score 1/2


def test_results_page_forbidden_to_non_owner(teacher_client, student_client):
    session_id = _new_session_id(teacher_client)
    _join(student_client, session_id)
    with teacher_client.websocket_connect(f"/ws/cours/{session_id}") as ws:
        _select_topic(ws)
        ws.send_json({"op": "qcm_prepare", "chapter_title": "Chapitre un"})
        ws.receive_json()

    db = SessionLocal()
    try:
        qid = db.query(QcmSession).filter(QcmSession.session_id == session_id).first().id
    finally:
        db.close()

    resp = student_client.get(f"/sessions/{session_id}/qcm/{qid}/results", follow_redirects=False)
    assert resp.status_code == 403
