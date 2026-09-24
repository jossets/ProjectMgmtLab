def test_home_page_lists_new_whiteboard_button(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Nouveau tableau blanc" in resp.text


def test_create_whiteboard_redirects_to_new_id(client):
    resp = client.post("/whiteboard/new", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/whiteboard/")


def test_whiteboard_page_loads(client, whiteboard_id):
    resp = client.get(f"/whiteboard/{whiteboard_id}")
    assert resp.status_code == 200
    assert "Nouveau tableau blanc" in resp.text


def test_new_whiteboard_starts_empty(client, whiteboard_id):
    resp = client.get(f"/api/whiteboard/{whiteboard_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Nouveau tableau blanc"
    assert data["elements"] == []


def test_unknown_but_well_formed_whiteboard_id_returns_404(client):
    assert client.get("/whiteboard/" + "a" * 32).status_code == 404
    assert client.get("/api/whiteboard/" + "b" * 32).status_code == 404


def test_rename_whiteboard(client, whiteboard_id):
    resp = client.patch(f"/api/whiteboard/{whiteboard_id}", json={"name": "Brainstorm"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Brainstorm"
    assert client.get(f"/api/whiteboard/{whiteboard_id}").json()["name"] == "Brainstorm"


def _sample_element(**overrides):
    element = {"type": "text", "x": 0, "y": 0, "width": 100, "height": 50, "z_index": 0, "data": {"content": "hi"}}
    element.update(overrides)
    return element


def test_websocket_create_persists_and_is_returned_by_rest(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        msg = ws.receive_json()

    assert msg["op"] == "created"
    assert msg["element"]["type"] == "text"
    element_id = msg["element"]["id"]

    elements = client.get(f"/api/whiteboard/{whiteboard_id}").json()["elements"]
    assert len(elements) == 1
    assert elements[0]["id"] == element_id


def test_websocket_update_and_delete_cycle(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        element_id = ws.receive_json()["element"]["id"]

        ws.send_json({"op": "update", "id": element_id, "element": _sample_element(x=10, y=20)})
        updated = ws.receive_json()
        assert updated["op"] == "updated"
        assert updated["element"]["x"] == 10
        assert updated["element"]["y"] == 20

        ws.send_json({"op": "delete", "id": element_id})
        deleted = ws.receive_json()
        assert deleted == {"op": "deleted", "id": element_id}

    assert client.get(f"/api/whiteboard/{whiteboard_id}").json()["elements"] == []


def test_websocket_update_accepts_negative_z_index(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        element_id = ws.receive_json()["element"]["id"]

        ws.send_json({"op": "update", "id": element_id, "element": _sample_element(z_index=-5)})
        updated = ws.receive_json()
        assert updated["op"] == "updated"
        assert updated["element"]["z_index"] == -5


def test_websocket_broadcasts_to_other_connected_clients(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws1:
        with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws2:
            ws1.send_json({"op": "create", "element": _sample_element()})
            msg1 = ws1.receive_json()
            msg2 = ws2.receive_json()
            assert msg1 == msg2


def test_websocket_create_echoes_client_ref_to_all_clients(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws1:
        with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws2:
            ws1.send_json({"op": "create", "element": _sample_element(), "client_ref": "abc123"})
            msg1 = ws1.receive_json()
            msg2 = ws2.receive_json()
    assert msg1["client_ref"] == "abc123"
    assert msg2["client_ref"] == "abc123"


def _run(text="", bold=False, italic=False, underline=False, strikethrough=False, font_size=16):
    return {
        "text": text,
        "bold": bold,
        "italic": italic,
        "underline": underline,
        "strikethrough": strikethrough,
        "font_size": font_size,
    }


def _para(runs, bullet=False):
    return {"bullet": bullet, "runs": runs}


def test_text_element_style_fields_round_trip(client, whiteboard_id):
    style = {
        "paragraphs": [
            _para([_run("Bonjour", bold=True, font_size=24)]),
            _para([_run("deuxième ligne", italic=True, underline=True, strikethrough=True)], bullet=True),
        ],
        "color": "#ff0000",
        "bg_color": "#00ff00",
        "border_color": "#0000ff",
        "reactions": {"heart": ["a" * 32, "b" * 32]},
    }
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element(data=style)})
        msg = ws.receive_json()

    assert msg["element"]["data"] == style


def test_text_element_data_defaults_are_filled_in(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element(data={})})
        msg = ws.receive_json()

    assert msg["element"]["data"] == {
        "paragraphs": [_para([_run()])],
        "color": "#1f2430",
        "bg_color": "#ffffff",
        "border_color": "#adb5bd",
        "reactions": {},
    }


def test_line_element_round_trips_points_and_style(client, whiteboard_id):
    line = {
        "type": "line",
        "x": 5,
        "y": 5,
        "width": 40,
        "height": 30,
        "z_index": 0,
        "data": {
            "points": [[0, 0], [10, 15], [40, 30]],
            "stroke_color": "#ff00aa",
            "stroke_width": 6,
            "arrow_start": True,
            "arrow_end": True,
        },
    }
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": line})
        msg = ws.receive_json()

    assert msg["op"] == "created"
    assert msg["element"]["type"] == "line"
    assert msg["element"]["data"]["points"] == [[0, 0], [10, 15], [40, 30]]
    assert msg["element"]["data"]["stroke_color"] == "#ff00aa"
    assert msg["element"]["data"]["stroke_width"] == 6
    assert msg["element"]["data"]["arrow_start"] is True
    assert msg["element"]["data"]["arrow_end"] is True

    elements = client.get(f"/api/whiteboard/{whiteboard_id}").json()["elements"]
    assert len(elements) == 1
    assert elements[0]["data"]["points"] == [[0, 0], [10, 15], [40, 30]]


def test_line_element_data_defaults_are_filled_in(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": {"type": "line", "x": 0, "y": 0, "width": 10, "height": 10, "z_index": 0, "data": {}}})
        msg = ws.receive_json()

    assert msg["element"]["data"] == {
        "points": [],
        "stroke_color": "#1f2430",
        "stroke_width": 3,
        "arrow_start": False,
        "arrow_end": False,
    }


def test_line_element_arrow_flags_can_be_toggled_via_update(client, whiteboard_id):
    line = {"type": "line", "x": 0, "y": 0, "width": 10, "height": 10, "z_index": 0, "data": {"points": [[0, 0], [10, 10]]}}
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": line})
        element_id = ws.receive_json()["element"]["id"]

        updated = dict(line, data={**line["data"], "arrow_end": True})
        ws.send_json({"op": "update", "id": element_id, "element": updated})
        msg = ws.receive_json()

    assert msg["element"]["data"]["arrow_start"] is False
    assert msg["element"]["data"]["arrow_end"] is True


def test_upload_valid_png_returns_url_and_serves_it(client, whiteboard_id, png_bytes):
    resp = client.post(
        f"/api/whiteboard/{whiteboard_id}/upload-image",
        files={"file": ("photo.png", png_bytes, "image/png")},
    )
    assert resp.status_code == 200
    url = resp.json()["url"]
    assert url.startswith(f"/uploads/whiteboard/{whiteboard_id}/")
    assert url.endswith(".png")

    served = client.get(url)
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content == png_bytes


def test_uploaded_image_can_be_referenced_by_image_element(client, whiteboard_id, png_bytes):
    upload = client.post(
        f"/api/whiteboard/{whiteboard_id}/upload-image",
        files={"file": ("photo.png", png_bytes, "image/png")},
    )
    url = upload.json()["url"]

    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json(
            {
                "op": "create",
                "element": {"type": "image", "x": 0, "y": 0, "width": 100, "height": 80, "z_index": 0, "data": {"src": url}},
            }
        )
        msg = ws.receive_json()

    assert msg["op"] == "created"
    assert msg["element"]["data"] == {"src": url}

    elements = client.get(f"/api/whiteboard/{whiteboard_id}").json()["elements"]
    assert elements[0]["data"]["src"] == url


def _cell(text="", bold=False, italic=False):
    return {"text": text, "bold": bold, "italic": italic}


def test_table_element_default_rows_are_5x2(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json(
            {
                "op": "create",
                "element": {"type": "table", "x": 0, "y": 0, "width": 200, "height": 150, "z_index": 0, "data": {}},
            }
        )
        msg = ws.receive_json()

    assert msg["op"] == "created"
    assert msg["element"]["data"]["rows"] == [[_cell(), _cell()] for _ in range(5)]
    assert msg["element"]["data"]["font_size"] == 14


def test_table_element_round_trips_custom_rows(client, whiteboard_id):
    rows = [[_cell("Nom", bold=True), _cell("Score", bold=True)], [_cell("Alice"), _cell("10", italic=True)], [_cell("Bob"), _cell("8")]]
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json(
            {
                "op": "create",
                "element": {"type": "table", "x": 0, "y": 0, "width": 200, "height": 150, "z_index": 0, "data": {"rows": rows}},
            }
        )
        msg = ws.receive_json()
        element_id = msg["element"]["id"]
        assert msg["element"]["data"]["rows"] == rows

        new_rows = [[_cell("Nom"), _cell("Score"), _cell("Niveau")], [_cell("Alice"), _cell("10"), _cell("A")], [_cell("Bob"), _cell("8"), _cell("B")]]
        ws.send_json(
            {
                "op": "update",
                "id": element_id,
                "element": {
                    "type": "table",
                    "x": 0,
                    "y": 0,
                    "width": 200,
                    "height": 150,
                    "z_index": 0,
                    "data": {"rows": new_rows, "font_size": 20},
                },
            }
        )
        updated = ws.receive_json()

    assert updated["element"]["data"]["rows"] == new_rows
    assert updated["element"]["data"]["font_size"] == 20

    elements = client.get(f"/api/whiteboard/{whiteboard_id}").json()["elements"]
    assert elements[0]["data"]["rows"] == new_rows


VOTER_A = "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1"
VOTER_B = "b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2"


def test_websocket_react_registers_first_vote(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        element_id = ws.receive_json()["element"]["id"]

        ws.send_json({"op": "react", "id": element_id, "reaction": "heart", "voter_id": VOTER_A})
        msg = ws.receive_json()

    assert msg["op"] == "updated"
    assert msg["element"]["data"]["reactions"] == {"heart": [VOTER_A]}

    elements = client.get(f"/api/whiteboard/{whiteboard_id}").json()["elements"]
    assert elements[0]["data"]["reactions"] == {"heart": [VOTER_A]}


def test_websocket_react_toggles_vote_off_on_second_click(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        element_id = ws.receive_json()["element"]["id"]

        ws.send_json({"op": "react", "id": element_id, "reaction": "heart", "voter_id": VOTER_A})
        ws.receive_json()

        ws.send_json({"op": "react", "id": element_id, "reaction": "heart", "voter_id": VOTER_A})
        msg = ws.receive_json()

    assert msg["element"]["data"]["reactions"] == {"heart": []}


def test_websocket_react_counts_distinct_voters_and_kinds(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        element_id = ws.receive_json()["element"]["id"]

        for voter, reaction in [(VOTER_A, "heart"), (VOTER_B, "heart"), (VOTER_A, "thumbsup")]:
            ws.send_json({"op": "react", "id": element_id, "reaction": reaction, "voter_id": voter})
            ws.receive_json()

        # same voter reacting "heart" again is a re-send, not a duplicate vote
        ws.send_json({"op": "react", "id": element_id, "reaction": "heart", "voter_id": VOTER_A})
        msg = ws.receive_json()

    # VOTER_A's second "heart" toggled their own vote off, leaving only VOTER_B
    assert msg["element"]["data"]["reactions"] == {"heart": [VOTER_B], "thumbsup": [VOTER_A]}


def test_websocket_react_broadcasts_to_other_connected_clients(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws1:
        ws1.send_json({"op": "create", "element": _sample_element()})
        element_id = ws1.receive_json()["element"]["id"]

        with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws2:
            ws1.send_json({"op": "react", "id": element_id, "reaction": "thumbsup", "voter_id": VOTER_A})
            msg1 = ws1.receive_json()
            msg2 = ws2.receive_json()
            assert msg1 == msg2
            assert msg1["element"]["data"]["reactions"] == {"thumbsup": [VOTER_A]}


def test_websocket_react_self_heals_legacy_count_shaped_reactions(client, whiteboard_id):
    # elements created while "reactions" was still a plain { kind: count }
    # dict (before per-voter tracking) can still be sitting in the DB;
    # voting on one must not crash, and must convert it to the new shape
    from app.db import SessionLocal
    from app.models import WhiteboardElement

    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        element_id = ws.receive_json()["element"]["id"]

    db = SessionLocal()
    try:
        element = db.get(WhiteboardElement, element_id)
        element.data = {**element.data, "reactions": {"heart": 3}}
        db.commit()
    finally:
        db.close()

    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "react", "id": element_id, "reaction": "heart", "voter_id": VOTER_A})
        msg = ws.receive_json()

    assert msg["op"] == "updated"
    assert msg["element"]["data"]["reactions"] == {"heart": [VOTER_A]}


def _session_with_whiteboard(teacher_client):
    session_id = teacher_client.post("/sessions/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    detail = teacher_client.get(f"/sessions/{session_id}")
    import re

    match = re.search(r"/whiteboard/([0-9a-f]{32})", detail.text)
    assert match
    return session_id, match.group(1)


def test_teacher_cursor_is_broadcast_to_other_viewers_but_not_echoed_back(teacher_client, student_client):
    session_id, whiteboard_id = _session_with_whiteboard(teacher_client)
    student_client.post(f"/join/{session_id}")

    with teacher_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as teacher_ws:
        with student_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as student_ws:
            teacher_ws.send_json({"op": "cursor", "x": 123.5, "y": 45.0})
            student_msg = student_ws.receive_json()
            assert student_msg["op"] == "cursor"
            assert student_msg["x"] == 123.5 and student_msg["y"] == 45.0
            assert isinstance(student_msg["id"], str) and student_msg["id"]
            assert student_msg["label"]  # the teacher's username

            # the teacher's own connection never gets its own cursor echoed —
            # prove it by having them send a *different*, response-producing
            # op right after and checking that's the first thing they get
            teacher_ws.send_json({"op": "create", "element": _sample_element()})
            teacher_msg = teacher_ws.receive_json()
            assert teacher_msg["op"] == "created"


def test_student_cursor_is_also_broadcast_to_others(teacher_client, student_client):
    # everyone connected shares their pointer, not just the teacher
    session_id, whiteboard_id = _session_with_whiteboard(teacher_client)
    student_client.post(f"/join/{session_id}")

    with student_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as student_ws:
        with teacher_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as teacher_ws:
            student_ws.send_json({"op": "cursor", "x": 7, "y": 8})
            msg = teacher_ws.receive_json()
            assert msg["op"] == "cursor"
            assert msg["x"] == 7 and msg["y"] == 8


def test_cursor_ids_differ_per_connection_of_the_same_user(student_client):
    whiteboard_id = student_client.post("/whiteboard/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    with student_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws_a:
        with student_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws_b:
            ws_a.send_json({"op": "cursor", "x": 1, "y": 1})
            msg_from_a = ws_b.receive_json()
            ws_b.send_json({"op": "cursor", "x": 2, "y": 2})
            msg_from_b = ws_a.receive_json()
    assert msg_from_a["id"] != msg_from_b["id"]


def test_cursor_gone_broadcast_when_a_viewer_disconnects(teacher_client, student_client):
    session_id, whiteboard_id = _session_with_whiteboard(teacher_client)
    student_client.post(f"/join/{session_id}")

    with teacher_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as teacher_ws:
        with student_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as student_ws:
            student_ws.send_json({"op": "cursor", "x": 1, "y": 1})
            cursor_msg = teacher_ws.receive_json()
            student_connection_id = cursor_msg["id"]

        gone_msg = teacher_ws.receive_json()
        assert gone_msg == {"op": "cursor_gone", "id": student_connection_id}


def test_editing_start_is_broadcast_to_other_viewers_but_not_echoed_back(teacher_client, student_client):
    session_id, whiteboard_id = _session_with_whiteboard(teacher_client)
    student_client.post(f"/join/{session_id}")

    with teacher_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as teacher_ws:
        teacher_ws.send_json({"op": "create", "element": _sample_element()})
        element_id = teacher_ws.receive_json()["element"]["id"]

        with student_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as student_ws:
            teacher_ws.send_json({"op": "editing", "id": element_id, "editing": True, "cell": None})
            student_msg = student_ws.receive_json()
            assert student_msg["op"] == "editing"
            assert student_msg["id"] == element_id
            assert student_msg["editing"] is True
            assert student_msg["cell"] is None
            assert student_msg["label"]  # the teacher's username
            assert isinstance(student_msg["editor_id"], str) and student_msg["editor_id"]

            # not echoed back to the sender: prove it the same way the cursor
            # tests do, by checking the next thing the teacher receives is
            # from an unrelated, response-producing op
            teacher_ws.send_json({"op": "create", "element": _sample_element()})
            teacher_msg = teacher_ws.receive_json()
            assert teacher_msg["op"] == "created"


def test_editing_stop_is_broadcast(teacher_client, student_client):
    session_id, whiteboard_id = _session_with_whiteboard(teacher_client)
    student_client.post(f"/join/{session_id}")

    with teacher_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as teacher_ws:
        teacher_ws.send_json({"op": "create", "element": _sample_element()})
        element_id = teacher_ws.receive_json()["element"]["id"]

        with student_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as student_ws:
            teacher_ws.send_json({"op": "editing", "id": element_id, "editing": True, "cell": None})
            student_ws.receive_json()
            teacher_ws.send_json({"op": "editing", "id": element_id, "editing": False, "cell": None})
            student_msg = student_ws.receive_json()
            assert student_msg["editing"] is False


def test_editing_on_a_table_cell_carries_its_row_and_column(teacher_client, student_client):
    session_id, whiteboard_id = _session_with_whiteboard(teacher_client)
    student_client.post(f"/join/{session_id}")
    table_element = {
        "type": "table",
        "x": 0,
        "y": 0,
        "width": 200,
        "height": 100,
        "z_index": 0,
        "data": {"rows": [[{"text": "a"}, {"text": "b"}]], "font_size": 14},
    }

    with teacher_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as teacher_ws:
        teacher_ws.send_json({"op": "create", "element": table_element})
        element_id = teacher_ws.receive_json()["element"]["id"]

        with student_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as student_ws:
            teacher_ws.send_json({"op": "editing", "id": element_id, "editing": True, "cell": [0, 1]})
            student_msg = student_ws.receive_json()
            assert student_msg["cell"] == [0, 1]


def test_editing_is_cleared_when_a_viewer_disconnects_mid_edit(teacher_client, student_client):
    # mirrors test_cursor_gone_broadcast_when_a_viewer_disconnects: someone
    # closing their tab mid-edit shouldn't leave a stale "editing" badge
    # stuck for everyone else forever
    session_id, whiteboard_id = _session_with_whiteboard(teacher_client)
    student_client.post(f"/join/{session_id}")

    with teacher_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as teacher_ws:
        teacher_ws.send_json({"op": "create", "element": _sample_element()})
        element_id = teacher_ws.receive_json()["element"]["id"]

        with student_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as student_ws:
            student_ws.send_json({"op": "editing", "id": element_id, "editing": True, "cell": None})
            teacher_ws.receive_json()

        # disconnect cleanup always sends cursor_gone first, then the
        # editing cleanup for whatever that connection had open
        cursor_gone_msg = teacher_ws.receive_json()
        assert cursor_gone_msg["op"] == "cursor_gone"
        gone_msg = teacher_ws.receive_json()
        assert gone_msg["op"] == "editing"
        assert gone_msg["id"] == element_id
        assert gone_msg["editing"] is False


def test_editing_stop_does_not_leak_after_normal_stop_then_disconnect(teacher_client, student_client):
    # a clean editing:false must not leave a phantom cleanup broadcast when
    # the connection later drops — pop_editing() should have nothing left
    session_id, whiteboard_id = _session_with_whiteboard(teacher_client)
    student_client.post(f"/join/{session_id}")

    with teacher_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as teacher_ws:
        teacher_ws.send_json({"op": "create", "element": _sample_element()})
        element_id = teacher_ws.receive_json()["element"]["id"]

        with student_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as student_ws:
            student_ws.send_json({"op": "editing", "id": element_id, "editing": True, "cell": None})
            teacher_ws.receive_json()
            student_ws.send_json({"op": "editing", "id": element_id, "editing": False, "cell": None})
            teacher_ws.receive_json()

        # student disconnects with nothing left open — teacher should get
        # only the cursor_gone cleanup, no extra "editing" broadcast
        gone_msg = teacher_ws.receive_json()
        assert gone_msg["op"] == "cursor_gone"


def test_websocket_does_not_hold_a_db_connection_while_idle(client, whiteboard_id):
    # regression guard: a connection used to keep one DB session (and its
    # underlying pool connection) checked out for its entire lifetime,
    # which exhausted the pool once enough boards were open at once —
    # everything else (including unrelated HTTP requests) then started
    # failing with QueuePool timeouts. Each message must now use its own
    # short-lived session, released the instant it's handled.
    from app.db import engine

    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        assert engine.pool.checkedout() == 0
        ws.send_json({"op": "create", "element": _sample_element()})
        ws.receive_json()
        assert engine.pool.checkedout() == 0


def test_toggle_lock_flips_state_and_broadcasts(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        element_id = ws.receive_json()["element"]["id"]
        assert client.get(f"/api/whiteboard/{whiteboard_id}").json()["elements"][0]["locked"] is False

        ws.send_json({"op": "toggle_lock", "id": element_id})
        msg = ws.receive_json()
        assert msg["op"] == "updated"
        assert msg["element"]["locked"] is True
        assert client.get(f"/api/whiteboard/{whiteboard_id}").json()["elements"][0]["locked"] is True

        ws.send_json({"op": "toggle_lock", "id": element_id})
        msg2 = ws.receive_json()
        assert msg2["element"]["locked"] is False


def test_toggle_lock_broadcasts_to_other_viewers(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws1:
        ws1.send_json({"op": "create", "element": _sample_element()})
        element_id = ws1.receive_json()["element"]["id"]

        with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws2:
            ws1.send_json({"op": "toggle_lock", "id": element_id})
            msg1 = ws1.receive_json()
            msg2 = ws2.receive_json()
            assert msg1 == msg2
            assert msg1["element"]["locked"] is True


def test_regular_update_does_not_reset_locked_state(client, whiteboard_id):
    # a plain content/position edit must never clobber the lock — locked is
    # deliberately kept out of the "update" op's own payload
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        element_id = ws.receive_json()["element"]["id"]
        ws.send_json({"op": "toggle_lock", "id": element_id})
        ws.receive_json()

        ws.send_json({"op": "update", "id": element_id, "element": _sample_element(x=42)})
        msg = ws.receive_json()
        assert msg["element"]["locked"] is True
        assert msg["element"]["x"] == 42


def _text_element_with_content(text):
    return _sample_element(
        type="text",
        data={
            "paragraphs": [{"bullet": False, "runs": [{"text": text}]}],
            "color": "#1f2430",
            "bg_color": "#ffffff",
            "border_color": "#adb5bd",
        },
    )


def test_delete_history_entry_includes_text_preview(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _text_element_with_content("Bonjour le monde")})
        element_id = ws.receive_json()["element"]["id"]
        ws.send_json({"op": "delete", "id": element_id})
        ws.receive_json()

    entries = client.get(f"/api/whiteboard/{whiteboard_id}/history").json()["entries"]
    assert entries[0]["action"] == "deleted"
    assert "Bonjour le monde" in entries[0]["summary"]
    assert entries[0]["can_restore"] is True


def test_restore_recreates_the_deleted_element(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _text_element_with_content("À restaurer")})
        original = ws.receive_json()["element"]
        ws.send_json({"op": "delete", "id": original["id"]})
        ws.receive_json()

        log_id = client.get(f"/api/whiteboard/{whiteboard_id}/history").json()["entries"][0]["id"]

        ws.send_json({"op": "restore", "log_id": log_id})
        msg = ws.receive_json()
        assert msg["op"] == "created"
        restored = msg["element"]
        # note: SQLite may reuse the same numeric id here since the table
        # was emptied by the delete — id equality alone doesn't tell you
        # whether this is a fresh row, so we don't assert on it either way
        assert restored["type"] == "text"
        assert restored["x"] == original["x"] and restored["y"] == original["y"]
        assert restored["data"]["paragraphs"][0]["runs"][0]["text"] == "À restaurer"

    elements = client.get(f"/api/whiteboard/{whiteboard_id}").json()["elements"]
    assert len(elements) == 1


def test_restore_preserves_locked_state(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        element_id = ws.receive_json()["element"]["id"]
        ws.send_json({"op": "toggle_lock", "id": element_id})
        ws.receive_json()
        ws.send_json({"op": "delete", "id": element_id})
        ws.receive_json()

        log_id = client.get(f"/api/whiteboard/{whiteboard_id}/history").json()["entries"][0]["id"]
        ws.send_json({"op": "restore", "log_id": log_id})
        msg = ws.receive_json()
        assert msg["element"]["locked"] is True


def test_non_delete_history_entries_are_not_restorable(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        ws.receive_json()

    entries = client.get(f"/api/whiteboard/{whiteboard_id}/history").json()["entries"]
    assert entries[0]["action"] == "created"
    assert entries[0]["can_restore"] is False
