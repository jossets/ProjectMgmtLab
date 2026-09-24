import pytest
from starlette.websockets import WebSocketDisconnect


def _sample_element(**overrides):
    element = {"type": "text", "x": 0, "y": 0, "width": 100, "height": 50, "z_index": 0, "data": {}}
    element.update(overrides)
    return element


@pytest.mark.parametrize(
    "bad_id",
    ["not-a-valid-id", "a" * 31, "a" * 33, "A" * 32, "1234-5678", "'; DROP TABLE whiteboards; --"],
)
def test_malformed_whiteboard_id_rejected_before_db_lookup(client, bad_id):
    assert client.get(f"/whiteboard/{bad_id}").status_code == 422
    assert client.get(f"/api/whiteboard/{bad_id}").status_code == 422
    assert client.patch(f"/api/whiteboard/{bad_id}", json={"name": "x"}).status_code == 422
    assert client.get(f"/api/whiteboard/{bad_id}/history").status_code == 422


def test_websocket_rejects_malformed_whiteboard_id(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/whiteboard/not-a-valid-id"):
            pass


def test_websocket_rejects_unknown_but_well_formed_whiteboard_id(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/whiteboard/" + "a" * 32):
            pass


def test_whiteboard_rename_over_max_length_rejected(client, whiteboard_id):
    resp = client.patch(f"/api/whiteboard/{whiteboard_id}", json={"name": "A" * 201})
    assert resp.status_code == 422


def test_websocket_rejects_invalid_element_type(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element(type="not-a-type")})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_negative_size(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element(width=-10)})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_out_of_range_coordinates(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element(x=999_999_999)})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_out_of_range_z_index(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element(z_index=-1_000_001)})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_too_many_text_paragraphs(client, whiteboard_id):
    paragraphs = [{"bullet": False, "runs": [{"text": "x"}]} for _ in range(201)]
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element(data={"paragraphs": paragraphs})})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_too_many_runs_in_a_paragraph(client, whiteboard_id):
    paragraphs = [{"bullet": False, "runs": [{"text": "x"} for _ in range(101)]}]
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element(data={"paragraphs": paragraphs})})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_malformed_json(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_text("not json at all")
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_unknown_operation(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "wipe-everything"})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_update_rejects_element_from_another_whiteboard(client, whiteboard_id):
    other_resp = client.post("/whiteboard/new", follow_redirects=False)
    other_id = other_resp.headers["location"].rsplit("/", 1)[-1]

    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws_a:
        ws_a.send_json({"op": "create", "element": _sample_element()})
        element_id = ws_a.receive_json()["element"]["id"]

    with client.websocket_connect(f"/ws/whiteboard/{other_id}") as ws_b:
        ws_b.send_json({"op": "update", "id": element_id, "element": _sample_element(x=999)})
        msg = ws_b.receive_json()
        assert msg["op"] == "error"

        ws_b.send_json({"op": "delete", "id": element_id})
        msg = ws_b.receive_json()
        assert msg["op"] == "error"

    # the element must be untouched on its real board
    elements = client.get(f"/api/whiteboard/{whiteboard_id}").json()["elements"]
    assert len(elements) == 1
    assert elements[0]["x"] == 0


def test_websocket_update_rejects_changing_element_type(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element(data={"paragraphs": [{"bullet": False, "runs": [{"text": "hi"}]}]})})
        element_id = ws.receive_json()["element"]["id"]

        ws.send_json(
            {
                "op": "update",
                "id": element_id,
                "element": {
                    "type": "line",
                    "x": 0,
                    "y": 0,
                    "width": 10,
                    "height": 10,
                    "z_index": 0,
                    "data": {"points": [[0, 0], [10, 10]]},
                },
            }
        )
        msg = ws.receive_json()
        assert msg["op"] == "error"

    elements = client.get(f"/api/whiteboard/{whiteboard_id}").json()["elements"]
    assert elements[0]["type"] == "text"
    assert elements[0]["data"]["paragraphs"][0]["runs"][0]["text"] == "hi"


VOTER_A = "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1"


def test_websocket_react_rejects_unknown_reaction_kind(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        element_id = ws.receive_json()["element"]["id"]

        ws.send_json({"op": "react", "id": element_id, "reaction": "party-parrot", "voter_id": VOTER_A})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_voter_id", ["not-hex-!!", "a" * 31, "a" * 33, "A" * 32, "", "'; DROP TABLE whiteboards; --"])
def test_websocket_react_rejects_malformed_voter_id(client, whiteboard_id, bad_voter_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        element_id = ws.receive_json()["element"]["id"]

        ws.send_json({"op": "react", "id": element_id, "reaction": "heart", "voter_id": bad_voter_id})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_react_rejects_missing_voter_id(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        element_id = ws.receive_json()["element"]["id"]

        ws.send_json({"op": "react", "id": element_id, "reaction": "heart"})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_react_rejects_nonexistent_element(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "react", "id": 999_999, "reaction": "heart", "voter_id": VOTER_A})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_react_rejects_element_from_another_whiteboard(client, whiteboard_id):
    other_resp = client.post("/whiteboard/new", follow_redirects=False)
    other_id = other_resp.headers["location"].rsplit("/", 1)[-1]

    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws_a:
        ws_a.send_json({"op": "create", "element": _sample_element()})
        element_id = ws_a.receive_json()["element"]["id"]

    with client.websocket_connect(f"/ws/whiteboard/{other_id}") as ws_b:
        ws_b.send_json({"op": "react", "id": element_id, "reaction": "heart", "voter_id": VOTER_A})
        msg = ws_b.receive_json()
        assert msg["op"] == "error"

    elements = client.get(f"/api/whiteboard/{whiteboard_id}").json()["elements"]
    assert elements[0]["data"]["reactions"] == {}


def test_websocket_react_rejects_non_text_element(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_line()})
        element_id = ws.receive_json()["element"]["id"]

        ws.send_json({"op": "react", "id": element_id, "reaction": "heart", "voter_id": VOTER_A})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_reaction_dict_with_disallowed_key(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element(data={"reactions": {"skull": [VOTER_A]}})})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_malformed_voter_id_in_reactions_payload(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element(data={"reactions": {"heart": ["<script>"]}})})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_too_many_voters_for_a_reaction(client, whiteboard_id):
    voters = [f"{i:032x}" for i in range(1001)]
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element(data={"reactions": {"heart": voters}})})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_delete_of_nonexistent_element_returns_error(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "delete", "id": 999_999})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_color", ["red", "#fff", "#gggggg", "rgb(0,0,0)", "#12345", "<script>"])
def test_websocket_rejects_invalid_text_color_format(client, whiteboard_id, bad_color):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element(data={"color": bad_color})})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_size", [0, 7, 97, -10, 1000])
def test_websocket_rejects_out_of_range_font_size(client, whiteboard_id, bad_size):
    paragraphs = [{"bullet": False, "runs": [{"text": "hi", "font_size": bad_size}]}]
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element(data={"paragraphs": paragraphs})})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_text_run_over_max_length(client, whiteboard_id):
    paragraphs = [{"bullet": False, "runs": [{"text": "A" * 2001}]}]
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element(data={"paragraphs": paragraphs})})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_text_content_is_never_reflected_as_html(client, whiteboard_id):
    xss = "<img src=x onerror=alert(1)>"
    paragraphs = [{"bullet": False, "runs": [{"text": xss}]}]
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element(data={"paragraphs": paragraphs})})
        msg = ws.receive_json()
    assert msg["op"] == "created"
    assert msg["element"]["data"]["paragraphs"][0]["runs"][0]["text"] == xss  # stored as plain text, never HTML

    page = client.get(f"/whiteboard/{whiteboard_id}")
    assert xss not in page.text


def test_websocket_ignores_unexpected_extra_fields_in_text_data(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json(
            {
                "op": "create",
                "element": _sample_element(data={"content": "hi", "onclick": "alert(1)", "__proto__": "x"}),
            }
        )
        msg = ws.receive_json()
    assert msg["op"] == "created"
    assert "onclick" not in msg["element"]["data"]
    assert "__proto__" not in msg["element"]["data"]


def _sample_line(**overrides):
    element = {"type": "line", "x": 0, "y": 0, "width": 10, "height": 10, "z_index": 0, "data": {}}
    element.update(overrides)
    return element


@pytest.mark.parametrize("bad_color", ["red", "#fff", "#gggggg", "rgb(0,0,0)", "<script>"])
def test_websocket_rejects_invalid_line_color_format(client, whiteboard_id, bad_color):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_line(data={"stroke_color": bad_color})})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_width", [0, 0.5, 41, -5, 1000])
def test_websocket_rejects_out_of_range_stroke_width(client, whiteboard_id, bad_width):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_line(data={"stroke_width": bad_width})})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_arrow", ["banana", [1, 2], {"a": 1}])
def test_websocket_rejects_non_boolean_arrow_flag(client, whiteboard_id, bad_arrow):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_line(data={"arrow_end": bad_arrow})})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_too_many_points_in_a_line(client, whiteboard_id):
    points = [[i, i] for i in range(2001)]
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_line(data={"points": points})})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_accepts_exactly_max_points_in_a_line(client, whiteboard_id):
    points = [[i, i] for i in range(2000)]
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_line(data={"points": points})})
        msg = ws.receive_json()
    assert msg["op"] == "created"


def test_websocket_rejects_malformed_points_shape(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_line(data={"points": ["not", "a", "point"]})})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_upload_rejects_non_image_content(client, whiteboard_id):
    resp = client.post(
        f"/api/whiteboard/{whiteboard_id}/upload-image",
        files={"file": ("evil.png", b"not actually a png, just text", "image/png")},
    )
    assert resp.status_code == 415


def test_upload_rejects_executable_disguised_as_image(client, whiteboard_id):
    resp = client.post(
        f"/api/whiteboard/{whiteboard_id}/upload-image",
        files={"file": ("evil.png", b"MZ\x90\x00" + b"\x00" * 100, "image/png")},
    )
    assert resp.status_code == 415


def test_upload_rejects_oversized_file(client, whiteboard_id):
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * 1024 * 1024 + 1)
    resp = client.post(
        f"/api/whiteboard/{whiteboard_id}/upload-image",
        files={"file": ("big.png", big, "image/png")},
    )
    assert resp.status_code == 413


def test_upload_rejects_empty_file(client, whiteboard_id):
    resp = client.post(
        f"/api/whiteboard/{whiteboard_id}/upload-image",
        files={"file": ("empty.png", b"", "image/png")},
    )
    assert resp.status_code == 400


def test_upload_to_unknown_whiteboard_returns_404(client, png_bytes):
    resp = client.post(
        f"/api/whiteboard/{'a' * 32}/upload-image",
        files={"file": ("photo.png", png_bytes, "image/png")},
    )
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "bad_filename",
    ["not-a-valid-name.png", "../../etc/passwd", "a" * 32 + ".exe", "a" * 32, "a" * 31 + ".png"],
)
def test_get_uploaded_image_rejects_malformed_filename(client, whiteboard_id, bad_filename):
    resp = client.get(f"/uploads/whiteboard/{whiteboard_id}/{bad_filename}")
    assert resp.status_code in (404, 422)


def test_get_uploaded_image_returns_404_for_missing_file(client, whiteboard_id):
    resp = client.get(f"/uploads/whiteboard/{whiteboard_id}/{'a' * 32}.png")
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "bad_src",
    ["https://evil.example/x.png", "javascript:alert(1)", "/etc/passwd", "../../uploads/whiteboard/x/y.png"],
)
def test_image_element_rejects_src_outside_upload_endpoint(client, whiteboard_id, bad_src):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json(
            {
                "op": "create",
                "element": {"type": "image", "x": 0, "y": 0, "width": 10, "height": 10, "z_index": 0, "data": {"src": bad_src}},
            }
        )
        msg = ws.receive_json()
    assert msg["op"] == "error"


def _cell(text="", bold=False, italic=False):
    return {"text": text, "bold": bold, "italic": italic}


def _sample_table(**overrides):
    element = {"type": "table", "x": 0, "y": 0, "width": 200, "height": 150, "z_index": 0, "data": {}}
    element.update(overrides)
    return element


def test_websocket_rejects_empty_table(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_table(data={"rows": []})})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_too_many_table_rows(client, whiteboard_id):
    rows = [[_cell("a"), _cell("b")] for _ in range(51)]
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_table(data={"rows": rows})})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_accepts_exactly_max_table_rows(client, whiteboard_id):
    rows = [[_cell("a"), _cell("b")] for _ in range(50)]
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_table(data={"rows": rows})})
        msg = ws.receive_json()
    assert msg["op"] == "created"


def test_websocket_rejects_too_many_table_columns(client, whiteboard_id):
    rows = [[_cell("x")] * 21]
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_table(data={"rows": rows})})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_ragged_table_rows(client, whiteboard_id):
    rows = [[_cell("a"), _cell("b")], [_cell("c")]]
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_table(data={"rows": rows})})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_table_cell_over_max_length(client, whiteboard_id):
    rows = [[_cell("A" * 501), _cell("b")]]
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_table(data={"rows": rows})})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_table_cell_content_is_never_reflected_as_html(client, whiteboard_id):
    xss = "<img src=x onerror=alert(1)>"
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_table(data={"rows": [[_cell(xss), _cell("b")]]})})
        msg = ws.receive_json()
    assert msg["op"] == "created"
    assert msg["element"]["data"]["rows"][0][0]["text"] == xss

    page = client.get(f"/whiteboard/{whiteboard_id}")
    assert xss not in page.text


@pytest.mark.parametrize("bad_size", [7, 49, 0, -5, 1000])
def test_websocket_rejects_out_of_range_table_font_size(client, whiteboard_id, bad_size):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_table(data={"font_size": bad_size})})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def _session_with_whiteboard(teacher_client):
    import re

    session_id = teacher_client.post("/sessions/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    detail = teacher_client.get(f"/sessions/{session_id}")
    match = re.search(r"/whiteboard/([0-9a-f]{32})", detail.text)
    assert match
    return session_id, match.group(1)


def test_admin_cursor_broadcast_on_a_board_with_no_session(client, admin_client):
    # everyone's pointer is relayed, on any board, session-attached or not
    whiteboard_id = client.post("/whiteboard/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    with admin_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as admin_ws:
        with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as other_ws:
            admin_ws.send_json({"op": "cursor", "x": 1, "y": 1})
            msg = other_ws.receive_json()
            assert msg["op"] == "cursor" and msg["x"] == 1 and msg["y"] == 1
            assert msg["label"] == "Admin"


def test_anonymous_cursor_is_broadcast_labelled_as_anonyme(client):
    whiteboard_id = client.post("/whiteboard/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as sender_ws:
        with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as other_ws:
            sender_ws.send_json({"op": "cursor", "x": 1, "y": 1})
            msg = other_ws.receive_json()
            assert msg["op"] == "cursor"
            assert msg["label"] == "Anonyme"


def test_admin_and_teacher_cursors_both_broadcast(teacher_client, admin_client):
    session_id, whiteboard_id = _session_with_whiteboard(teacher_client)
    with admin_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as admin_ws:
        with teacher_client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as teacher_ws:
            admin_ws.send_json({"op": "cursor", "x": 5, "y": 5})
            msg = teacher_ws.receive_json()
            assert msg["op"] == "cursor" and msg["x"] == 5 and msg["y"] == 5


@pytest.mark.parametrize("bad_coord", [2_000_000, -2_000_000])
def test_cursor_rejects_out_of_range_coordinates(client, bad_coord):
    whiteboard_id = client.post("/whiteboard/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "cursor", "x": bad_coord, "y": 0})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_cursor_payload_missing_fields_rejected(client):
    whiteboard_id = client.post("/whiteboard/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "cursor", "x": 1})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_toggle_lock_rejects_unknown_id(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "toggle_lock", "id": 999999})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_toggle_lock_rejects_element_from_another_board(client, whiteboard_id):
    other_id = client.post("/whiteboard/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    with client.websocket_connect(f"/ws/whiteboard/{other_id}") as other_ws:
        other_ws.send_json({"op": "create", "element": _sample_element()})
        other_element_id = other_ws.receive_json()["element"]["id"]

    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "toggle_lock", "id": other_element_id})
        msg = ws.receive_json()
    assert msg["op"] == "error"

    # the other board's element must be untouched
    other_elements = client.get(f"/api/whiteboard/{other_id}").json()["elements"]
    assert other_elements[0]["locked"] is False


@pytest.mark.parametrize("bad_id", [0, -1, "not-an-int"])
def test_toggle_lock_rejects_invalid_id(client, whiteboard_id, bad_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "toggle_lock", "id": bad_id})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_restore_rejects_unknown_log_id(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "restore", "log_id": 999999})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_elements_beyond_max(client, whiteboard_id):
    # PROJECTMGR_MAX_WHITEBOARD_ELEMENTS is set to 5 for tests (see conftest.py)
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        for _ in range(5):
            ws.send_json({"op": "create", "element": _sample_element()})
            assert ws.receive_json()["op"] == "created"

        ws.send_json({"op": "create", "element": _sample_element()})
        msg = ws.receive_json()
    assert msg["op"] == "error"

    # the board must still only have the 5 that made it in
    elements = client.get(f"/api/whiteboard/{whiteboard_id}").json()["elements"]
    assert len(elements) == 5


def test_restore_rejects_when_board_is_already_at_max_elements(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        first_id = ws.receive_json()["element"]["id"]
        ws.send_json({"op": "delete", "id": first_id})
        deleted_msg = ws.receive_json()
        assert deleted_msg["op"] == "deleted"

        for _ in range(5):
            ws.send_json({"op": "create", "element": _sample_element()})
            assert ws.receive_json()["op"] == "created"

        entries = client.get(f"/api/whiteboard/{whiteboard_id}/history").json()["entries"]
        log_id = next(e["id"] for e in entries if e["can_restore"])
        ws.send_json({"op": "restore", "log_id": log_id})
        msg = ws.receive_json()
    assert msg["op"] == "error"

    elements = client.get(f"/api/whiteboard/{whiteboard_id}").json()["elements"]
    assert len(elements) == 5


def test_editing_rejects_unknown_id(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "editing", "id": 999999, "editing": True, "cell": None})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_editing_rejects_element_from_another_board(client, whiteboard_id):
    other_id = client.post("/whiteboard/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    with client.websocket_connect(f"/ws/whiteboard/{other_id}") as other_ws:
        other_ws.send_json({"op": "create", "element": _sample_element()})
        other_element_id = other_ws.receive_json()["element"]["id"]

    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "editing", "id": other_element_id, "editing": True, "cell": None})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_id", [0, -1, "not-an-int"])
def test_editing_rejects_invalid_id(client, whiteboard_id, bad_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "editing", "id": bad_id, "editing": True, "cell": None})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_editing_payload_missing_fields_rejected(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "editing", "id": 1})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_cell", [[-1, 0], [0, -1], [1001, 0], [0, 1001]])
def test_editing_rejects_out_of_range_cell(client, whiteboard_id, bad_cell):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        element_id = ws.receive_json()["element"]["id"]
        ws.send_json({"op": "editing", "id": element_id, "editing": True, "cell": bad_cell})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_editing_not_persisted_or_exposed_over_rest(client, whiteboard_id):
    # purely ephemeral — never written to the element, never shows up in
    # the plain REST snapshot of the board
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        element_id = ws.receive_json()["element"]["id"]
        with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as other_ws:
            ws.send_json({"op": "editing", "id": element_id, "editing": True, "cell": None})
            # wait for the broadcast so the server has definitely finished
            # handling the "editing" op before checking the REST snapshot
            other_ws.receive_json()

    element = client.get(f"/api/whiteboard/{whiteboard_id}").json()["elements"][0]
    assert "editing" not in element


def test_restore_rejects_log_entry_from_another_board(client, whiteboard_id):
    other_id = client.post("/whiteboard/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    with client.websocket_connect(f"/ws/whiteboard/{other_id}") as other_ws:
        other_ws.send_json({"op": "create", "element": _sample_element()})
        other_element_id = other_ws.receive_json()["element"]["id"]
        other_ws.send_json({"op": "delete", "id": other_element_id})
        other_ws.receive_json()

    other_log_id = client.get(f"/api/whiteboard/{other_id}/history").json()["entries"][0]["id"]

    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "restore", "log_id": other_log_id})
        msg = ws.receive_json()
    assert msg["op"] == "error"
    assert client.get(f"/api/whiteboard/{whiteboard_id}").json()["elements"] == []


def test_restore_rejects_a_non_deleted_log_entry(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        ws.receive_json()

    created_log_id = client.get(f"/api/whiteboard/{whiteboard_id}/history").json()["entries"][0]["id"]

    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "restore", "log_id": created_log_id})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_id", [0, -1, "not-an-int"])
def test_restore_rejects_invalid_log_id(client, whiteboard_id, bad_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "restore", "log_id": bad_id})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_restored_text_content_never_reflected_as_html(client, whiteboard_id):
    xss = "<img src=x onerror=alert(1)>"
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json(
            {
                "op": "create",
                "element": _sample_element(
                    type="text",
                    data={
                        "paragraphs": [{"bullet": False, "runs": [{"text": xss}]}],
                        "color": "#1f2430",
                        "bg_color": "#ffffff",
                        "border_color": "#adb5bd",
                    },
                ),
            }
        )
        element_id = ws.receive_json()["element"]["id"]
        ws.send_json({"op": "delete", "id": element_id})
        ws.receive_json()

    history = client.get(f"/api/whiteboard/{whiteboard_id}/history").json()["entries"]
    log_id = history[0]["id"]
    assert xss in history[0]["summary"]  # stored as harmless text, same as everywhere else

    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "restore", "log_id": log_id})
        ws.receive_json()

    page = client.get(f"/whiteboard/{whiteboard_id}")
    assert xss not in page.text
