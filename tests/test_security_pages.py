import pytest
from starlette.websockets import WebSocketDisconnect


@pytest.mark.parametrize(
    "bad_id",
    ["not-a-valid-id", "a" * 31, "a" * 33, "A" * 32, "1234-5678", "'; DROP TABLE page_boards; --"],
)
def test_malformed_page_board_id_rejected_before_db_lookup(client, bad_id):
    assert client.get(f"/pages/{bad_id}").status_code == 422
    assert client.get(f"/api/pages/{bad_id}").status_code == 422
    assert client.patch(f"/api/pages/{bad_id}", json={"name": "x"}).status_code == 422
    assert client.get(f"/api/pages/{bad_id}/history").status_code == 422
    assert (
        client.post(f"/api/pages/{bad_id}/upload-image", files={"file": ("x.png", b"x", "image/png")}).status_code == 422
    )
    assert client.get(f"/uploads/pages/{bad_id}/" + "a" * 32 + ".png").status_code == 422


def test_websocket_rejects_malformed_page_board_id(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/pages/not-a-valid-id"):
            pass


def test_websocket_rejects_unknown_but_well_formed_page_board_id(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/pages/" + "a" * 32):
            pass


def test_pages_rename_over_max_length_rejected(client, page_board_id):
    resp = client.patch(f"/api/pages/{page_board_id}", json={"name": "A" * 201})
    assert resp.status_code == 422


def test_websocket_rejects_malformed_json(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_text("not json at all")
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_unknown_operation(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "not_a_real_op"})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_overlong_title(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_page", "parent_id": None, "title": "A" * 201})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_id", [0, -1, "not-an-int"])
def test_websocket_rejects_invalid_page_id(client, page_board_id, bad_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "rename_page", "id": bad_id, "title": "x"})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rename_rejects_unknown_id(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "rename_page", "id": 999999, "title": "x"})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_page_id_from_another_board(client, page_board_id):
    other_board_id = client.post("/pages/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    with client.websocket_connect(f"/ws/pages/{other_board_id}") as other_ws:
        other_ws.send_json({"op": "create_page", "parent_id": None, "title": "Ailleurs"})
        other_page_id = other_ws.receive_json()["page"]["id"]

    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "rename_page", "id": other_page_id, "title": "Vol"})
        rename_msg = ws.receive_json()
        ws.send_json({"op": "delete_page", "id": other_page_id})
        delete_msg = ws.receive_json()
        ws.send_json({"op": "move_page", "id": other_page_id, "parent_id": None, "index": 0})
        move_msg = ws.receive_json()
    assert rename_msg["op"] == delete_msg["op"] == move_msg["op"] == "error"

    # untouched on its real board
    other_data = client.get(f"/api/pages/{other_board_id}").json()
    assert other_data["pages"][0]["title"] == "Ailleurs"


def test_websocket_create_page_rejects_parent_from_another_board(client, page_board_id):
    other_board_id = client.post("/pages/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    with client.websocket_connect(f"/ws/pages/{other_board_id}") as other_ws:
        other_ws.send_json({"op": "create_page", "parent_id": None, "title": "Parent ailleurs"})
        other_parent_id = other_ws.receive_json()["page"]["id"]

    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_page", "parent_id": other_parent_id, "title": "Intrus"})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_move_page_rejects_parent_from_another_board(client, page_board_id):
    other_board_id = client.post("/pages/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    with client.websocket_connect(f"/ws/pages/{other_board_id}") as other_ws:
        other_ws.send_json({"op": "create_page", "parent_id": None, "title": "Parent ailleurs"})
        other_parent_id = other_ws.receive_json()["page"]["id"]

    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_page", "parent_id": None, "title": "Locale"})
        local_id = ws.receive_json()["page"]["id"]
        ws.send_json({"op": "move_page", "id": local_id, "parent_id": other_parent_id, "index": 0})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_restore_rejects_unknown_log_id(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "restore", "log_id": 999999})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_restore_rejects_a_non_deleted_entry(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_page", "parent_id": None, "title": "Toujours là"})
        ws.receive_json()

    entries = client.get(f"/api/pages/{page_board_id}/history").json()["entries"]
    created_entry_id = next(e["id"] for e in entries if e["action"] == "created")

    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "restore", "log_id": created_entry_id})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def _sample_text_block(text="x"):
    return {"type": "text", "data": {"paragraphs": [{"bullet": False, "runs": [{"text": text}]}]}}


def _create_page(ws, title="Page", parent_id=None):
    ws.send_json({"op": "create_page", "parent_id": parent_id, "title": title})
    return ws.receive_json()["page"]["id"]


def test_websocket_rejects_invalid_block_type(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "create_block", "page_id": page_id, "block": {"type": "not-a-type", "data": {}}})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_create_block_rejects_unknown_page_id(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_block", "page_id": 999999, "block": _sample_text_block()})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_create_block_rejects_page_from_another_board(client, page_board_id):
    other_board_id = client.post("/pages/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    with client.websocket_connect(f"/ws/pages/{other_board_id}") as other_ws:
        other_page_id = _create_page(other_ws, "Ailleurs")

    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_block", "page_id": other_page_id, "block": _sample_text_block()})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_block_id_from_another_board(client, page_board_id):
    other_board_id = client.post("/pages/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    with client.websocket_connect(f"/ws/pages/{other_board_id}") as other_ws:
        other_page_id = _create_page(other_ws, "Ailleurs")
        other_ws.send_json({"op": "create_block", "page_id": other_page_id, "block": _sample_text_block("intact")})
        other_block_id = other_ws.receive_json()["block"]["id"]

    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "update_block", "id": other_block_id, "block": _sample_text_block("vol")})
        update_msg = ws.receive_json()
        ws.send_json({"op": "move_block", "id": other_block_id, "index": 0})
        move_msg = ws.receive_json()
        ws.send_json({"op": "delete_block", "id": other_block_id})
        delete_msg = ws.receive_json()
    assert update_msg["op"] == move_msg["op"] == delete_msg["op"] == "error"

    other_data = client.get(f"/api/pages/{other_board_id}").json()
    assert other_data["blocks"][0]["data"]["paragraphs"][0]["runs"][0]["text"] == "intact"


@pytest.mark.parametrize("bad_id", [0, -1, "not-an-int"])
def test_websocket_rejects_invalid_block_id(client, page_board_id, bad_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "update_block", "id": bad_id, "block": _sample_text_block()})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_update_block_rejects_type_change(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "create_block", "page_id": page_id, "block": _sample_text_block()})
        block_id = ws.receive_json()["block"]["id"]

        ws.send_json({"op": "update_block", "id": block_id, "block": {"type": "link", "data": {}}})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_overlong_run_text(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "text", "data": {"paragraphs": [{"bullet": False, "runs": [{"text": "A" * 2001}]}]}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_heading", [-1, 5, 100])
def test_websocket_rejects_out_of_range_heading(client, page_board_id, bad_heading):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "text", "data": {"paragraphs": [{"bullet": False, "heading": bad_heading, "runs": [{"text": "x"}]}]}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_image_upload_rejects_oversized_file(client, page_board_id):
    big = b"\x89PNG\r\n\x1a\n" + b"a" * (5 * 1024 * 1024 + 1)
    resp = client.post(
        f"/api/pages/{page_board_id}/upload-image",
        files={"file": ("photo.png", big, "image/png")},
    )
    assert resp.status_code == 413


def test_image_upload_rejects_non_image_content(client, page_board_id):
    resp = client.post(
        f"/api/pages/{page_board_id}/upload-image",
        files={"file": ("evil.png", b"not a real image", "image/png")},
    )
    assert resp.status_code == 415


@pytest.mark.parametrize("bad_filename", ["not-a-valid-name.png", "a" * 32 + ".exe", "a" * 31 + ".png"])
def test_uploaded_image_rejects_malformed_filename(client, page_board_id, bad_filename):
    resp = client.get(f"/uploads/pages/{page_board_id}/{bad_filename}")
    assert resp.status_code == 422


def test_uploaded_image_path_traversal_does_not_escape_the_upload_dir(client, page_board_id):
    # the URL itself gets normalized before routing (so this never even
    # reaches our handler as literal "../" text) — assert the safe outcome
    # (never a 200 serving something outside the uploads dir) rather than
    # a specific status code
    resp = client.get(f"/uploads/pages/{page_board_id}/../../../etc/passwd")
    assert resp.status_code != 200


def test_image_block_rejects_src_outside_uploads(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "image", "data": {"src": "/etc/passwd"}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_width", [10, 2001, -5])
def test_image_block_rejects_out_of_range_width(client, page_board_id, bad_width):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "image", "data": {"src": "/uploads/pages/x/y.png", "width": bad_width}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_image_upload_rejects_non_member(client, teacher_client):
    session_id, board_id = _session_with_page_board(teacher_client)
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 20
    resp = client.post(
        f"/api/pages/{board_id}/upload-image",
        files={"file": ("photo.png", png_bytes, "image/png")},
    )
    assert resp.status_code == 403


def test_code_block_rejects_invalid_language(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "code", "data": {"code": "x", "language": "not-a-real-language"}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_code_block_rejects_overlong_code(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "code", "data": {"code": "A" * 20_001}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_url", ["javascript:alert(1)", "data:text/html,<script>1</script>", "ftp://example.com/x"])
def test_link_block_rejects_non_http_schemes(client, page_board_id, bad_url):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "link", "data": {"url": bad_url}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_link_block_allows_empty_url_on_creation(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "create_block", "page_id": page_id, "block": {"type": "link", "data": {}}})
        msg = ws.receive_json()
    assert msg["op"] == "block_created"
    assert msg["block"]["data"]["url"] == ""


def test_table_block_rejects_mismatched_column_counts(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "table", "data": {"rows": [[{"text": "a"}, {"text": "b"}], [{"text": "c"}]]}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_table_block_rejects_too_many_columns(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "table", "data": {"rows": [[{"text": "x"} for _ in range(21)]]}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_restore_block_rejects_a_deleted_entry_from_another_board(client, page_board_id):
    other_board_id = client.post("/pages/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    with client.websocket_connect(f"/ws/pages/{other_board_id}") as other_ws:
        other_page_id = _create_page(other_ws, "Ailleurs")
        other_ws.send_json({"op": "create_block", "page_id": other_page_id, "block": _sample_text_block()})
        other_block_id = other_ws.receive_json()["block"]["id"]
        other_ws.send_json({"op": "delete_block", "id": other_block_id})
        other_ws.receive_json()

    other_entries = client.get(f"/api/pages/{other_board_id}/history").json()["entries"]
    log_id = next(e["id"] for e in other_entries if e["can_restore"])

    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "restore", "log_id": log_id})
        msg = ws.receive_json()
    assert msg["op"] == "error"
    assert client.get(f"/api/pages/{page_board_id}").json()["blocks"] == []


def test_restore_block_rejects_when_its_page_was_also_deleted(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws, "Temporaire")
        ws.send_json({"op": "create_block", "page_id": page_id, "block": _sample_text_block()})
        block_id = ws.receive_json()["block"]["id"]
        ws.send_json({"op": "delete_block", "id": block_id})
        ws.receive_json()
        ws.send_json({"op": "delete_page", "id": page_id})
        ws.receive_json()

        entries = client.get(f"/api/pages/{page_board_id}/history").json()["entries"]
        block_log_id = next(e["id"] for e in entries if e["action"] == "deleted" and "Bloc" in e["summary"])
        ws.send_json({"op": "restore", "log_id": block_log_id})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_restore_rejects_a_deleted_entry_from_another_board(client, page_board_id):
    other_board_id = client.post("/pages/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    with client.websocket_connect(f"/ws/pages/{other_board_id}") as other_ws:
        other_ws.send_json({"op": "create_page", "parent_id": None, "title": "Ailleurs"})
        other_page_id = other_ws.receive_json()["page"]["id"]
        other_ws.send_json({"op": "delete_page", "id": other_page_id})
        other_ws.receive_json()

    other_entries = client.get(f"/api/pages/{other_board_id}/history").json()["entries"]
    log_id = next(e["id"] for e in other_entries if e["can_restore"])

    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "restore", "log_id": log_id})
        msg = ws.receive_json()
    assert msg["op"] == "error"
    assert client.get(f"/api/pages/{page_board_id}").json()["pages"] == []


def _session_with_page_board(teacher_client):
    session_id = teacher_client.post("/sessions/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    board_id = teacher_client.post(
        "/pages/new", data={"session_id": session_id}, follow_redirects=False
    ).headers["location"].rsplit("/", 1)[-1]
    return session_id, board_id


def test_attached_page_board_rejects_non_member_and_allows_member(client, teacher_client, student_client):
    session_id, board_id = _session_with_page_board(teacher_client)

    denied = client.get(f"/pages/{board_id}", follow_redirects=False)
    assert denied.status_code == 303
    assert denied.headers["location"] == "/login"

    student_client.post(f"/join/{session_id}", follow_redirects=False)
    allowed = student_client.get(f"/pages/{board_id}")
    assert allowed.status_code == 200

    # the owning teacher also has access without being an explicit member
    owner_access = teacher_client.get(f"/pages/{board_id}")
    assert owner_access.status_code == 200


def test_blocked_session_denies_page_board_access_even_to_members(teacher_client, student_client):
    session_id, board_id = _session_with_page_board(teacher_client)
    student_client.post(f"/join/{session_id}", follow_redirects=False)
    assert student_client.get(f"/pages/{board_id}").status_code == 200

    teacher_client.post(f"/sessions/{session_id}/toggle-active")  # now blocked
    resp = student_client.get(f"/pages/{board_id}", follow_redirects=False)
    assert resp.status_code == 403

    # the owning teacher can still manage it regardless of the block
    assert teacher_client.get(f"/pages/{board_id}").status_code == 200


def test_admin_always_has_access_to_a_session_scoped_page_board(teacher_client, admin_client):
    _session_id, board_id = _session_with_page_board(teacher_client)
    assert admin_client.get(f"/pages/{board_id}").status_code == 200


def test_unattached_page_board_remains_open_to_anyone(client):
    from fastapi.testclient import TestClient

    from app.main import app

    board_id = client.post("/pages/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    anon = TestClient(app)
    assert anon.get(f"/pages/{board_id}").status_code == 200


def test_new_page_board_with_session_rejects_non_owner(admin_client, teacher_client):
    import uuid

    from fastapi.testclient import TestClient

    from app.main import app

    session_id = teacher_client.post("/sessions/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]

    username = f"otherteacher_{uuid.uuid4().hex[:10]}"
    admin_client.post("/accounts/teachers", data={"username": username, "password": "Sup3rSecret!"}, follow_redirects=False)
    intruder = TestClient(app)
    resp = intruder.post("/login", data={"username": username, "password": "Sup3rSecret!"}, follow_redirects=False)
    assert resp.status_code == 303

    resp = intruder.post("/pages/new", data={"session_id": session_id})
    assert resp.status_code == 403


@pytest.mark.parametrize("bad_session_id", ["short", "toolongforthis", "abcdefg", "3d6gh75"])
def test_new_page_board_rejects_malformed_session_id(teacher_client, bad_session_id):
    resp = teacher_client.post("/pages/new", data={"session_id": bad_session_id})
    assert resp.status_code == 422


def test_new_page_board_rejects_unknown_session_id(teacher_client):
    resp = teacher_client.post("/pages/new", data={"session_id": "ZZZZZZZ"})
    assert resp.status_code == 404


# ---- input type confusion — every field must reject a value of the wrong
# JSON type, not just an out-of-range value of the right type. Pydantic v2's
# default lax mode does coerce some values across related types (numeric
# strings -> int, "yes"/"no" -> bool, bool <-> int since bool is an int
# subclass) — those cases are noted inline rather than asserted as errors,
# since asserting rejection there would just be testing a wrong expectation. ----


@pytest.mark.parametrize("bad_title", [123, ["x"], {"a": 1}, None])
def test_websocket_create_page_rejects_non_string_title(client, page_board_id, bad_title):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_page", "parent_id": None, "title": bad_title})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_parent_id", ["not-an-int", [1], {"id": 1}, 1.5])
def test_websocket_create_page_rejects_non_integer_parent_id(client, page_board_id, bad_parent_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_page", "parent_id": bad_parent_id, "title": "x"})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_index", ["not-an-int", [0], -1, 1.5])
def test_websocket_move_page_rejects_non_integer_index(client, page_board_id, bad_index):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "move_page", "id": page_id, "parent_id": None, "index": bad_index})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_create_block_rejects_missing_block_field(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "create_block", "page_id": page_id})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_create_block_rejects_block_missing_type(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "create_block", "page_id": page_id, "block": {"data": {}}})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_type", [123, ["text"], None, True])
def test_websocket_create_block_rejects_non_string_block_type(client, page_board_id, bad_type):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "create_block", "page_id": page_id, "block": {"type": bad_type, "data": {}}})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_data", ["not-a-dict", [1, 2, 3], 42])
def test_websocket_create_block_rejects_non_dict_data(client, page_board_id, bad_data):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "create_block", "page_id": page_id, "block": {"type": "text", "data": bad_data}})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_page_id", ["not-an-int", [1], {"id": 1}, 1.5])
def test_websocket_create_block_rejects_non_integer_page_id(client, page_board_id, bad_page_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"op": "create_block", "page_id": bad_page_id, "block": _sample_text_block()})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_bullet", ["maybe", ["x"], {"a": 1}])
def test_websocket_rejects_non_boolean_bullet(client, page_board_id, bad_bullet):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "text", "data": {"paragraphs": [{"bullet": bad_bullet, "runs": [{"text": "x"}]}]}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_framed", ["maybe", ["x"], 42])
def test_websocket_rejects_non_boolean_framed(client, page_board_id, bad_framed):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "text", "data": {"framed": bad_framed}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_heading", ["two", ["x"], {"a": 1}, 1.5])
def test_websocket_rejects_non_integer_heading(client, page_board_id, bad_heading):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "text", "data": {"paragraphs": [{"heading": bad_heading, "runs": [{"text": "x"}]}]}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_font_size", [7, 97, -5, "big"])
def test_websocket_rejects_invalid_text_run_font_size(client, page_board_id, bad_font_size):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "text", "data": {"paragraphs": [{"runs": [{"text": "x", "font_size": bad_font_size}]}]}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_font_size", [7, 49, -1, "huge"])
def test_websocket_rejects_invalid_table_font_size(client, page_board_id, bad_font_size):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "table", "data": {"font_size": bad_font_size}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_src", [123, ["x"], None])
def test_websocket_rejects_non_string_image_src(client, page_board_id, bad_src):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "image", "data": {"src": bad_src}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_overlong_image_caption(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "image", "data": {"src": "/uploads/pages/x/y.png", "caption": "A" * 301}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize(
    "overlong_field,value",
    [("title", "A" * 301), ("description", "A" * 501)],
)
def test_websocket_rejects_overlong_link_fields(client, page_board_id, overlong_field, value):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "link", "data": {"url": "", overlong_field: value}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_overlong_link_url(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "link", "data": {"url": "https://example.com/" + "a" * 2000}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_language", [123, ["python"], None])
def test_websocket_rejects_non_string_code_language(client, page_board_id, bad_language):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        block = {"type": "code", "data": {"code": "x", "language": bad_language}}
        ws.send_json({"op": "create_block", "page_id": page_id, "block": block})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_overlong_client_ref(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json(
            {"op": "create_block", "page_id": page_id, "block": _sample_text_block(), "client_ref": "r" * 65}
        )
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_client_ref", [123, ["x"], {"a": 1}])
def test_websocket_rejects_non_string_client_ref(client, page_board_id, bad_client_ref):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json(
            {"op": "create_block", "page_id": page_id, "block": _sample_text_block(), "client_ref": bad_client_ref}
        )
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_non_string_rename_title(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        page_id = _create_page(ws)
        ws.send_json({"op": "rename_page", "id": page_id, "title": {"nested": "object"}})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_non_string_board_rename_name(client, page_board_id):
    resp = client.patch(f"/api/pages/{page_board_id}", json={"name": 12345})
    assert resp.status_code == 422


def test_websocket_rejects_payload_that_is_not_a_json_object(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json(["op", "create_page"])
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_payload_missing_op(client, page_board_id):
    with client.websocket_connect(f"/ws/pages/{page_board_id}") as ws:
        ws.send_json({"title": "no op key here"})
        msg = ws.receive_json()
    assert msg["op"] == "error"
