import pytest
from starlette.websockets import WebSocketDisconnect


@pytest.mark.parametrize(
    "bad_id",
    ["not-a-valid-id", "a" * 31, "a" * 33, "A" * 32, "1234-5678", "'; DROP TABLE kanban_boards; --"],
)
def test_malformed_kanban_id_rejected_before_db_lookup(client, bad_id):
    assert client.get(f"/kanban/{bad_id}").status_code == 422
    assert client.get(f"/api/kanban/{bad_id}").status_code == 422
    assert client.patch(f"/api/kanban/{bad_id}", json={"name": "x"}).status_code == 422
    assert client.get(f"/api/kanban/{bad_id}/history").status_code == 422


def test_websocket_rejects_malformed_kanban_id(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/kanban/not-a-valid-id"):
            pass


def test_websocket_rejects_unknown_but_well_formed_kanban_id(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/kanban/" + "a" * 32):
            pass


def test_kanban_rename_over_max_length_rejected(client, kanban_id):
    resp = client.patch(f"/api/kanban/{kanban_id}", json={"name": "A" * 201})
    assert resp.status_code == 422


def test_websocket_rejects_malformed_json(client, kanban_id):
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_text("not json at all")
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_unknown_operation(client, kanban_id):
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "wipe-everything"})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def _first_column_id(client, kanban_id):
    return client.get(f"/api/kanban/{kanban_id}").json()["columns"][0]["id"]


def test_websocket_rejects_column_title_over_max_length(client, kanban_id):
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "create_column", "title": "A" * 101})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_columns_beyond_max(client, kanban_id):
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        # board already starts with 3 default columns; MAX_COLUMNS is 20
        for _ in range(17):
            ws.send_json({"op": "create_column", "title": "x"})
            assert ws.receive_json()["op"] == "column_created"

        ws.send_json({"op": "create_column", "title": "one too many"})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rename_nonexistent_column_returns_error(client, kanban_id):
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "rename_column", "id": 999_999, "title": "x"})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_delete_nonexistent_column_returns_error(client, kanban_id):
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "delete_column", "id": 999_999})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_move_nonexistent_column_returns_error(client, kanban_id):
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "move_column", "id": 999_999, "index": 0})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_column_op_rejects_column_from_another_board(client, kanban_id):
    other_resp = client.post("/kanban/new", follow_redirects=False)
    other_id = other_resp.headers["location"].rsplit("/", 1)[-1]
    other_column_id = _first_column_id(client, other_id)

    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "rename_column", "id": other_column_id, "title": "hacked"})
        msg = ws.receive_json()
        assert msg["op"] == "error"

        ws.send_json({"op": "delete_column", "id": other_column_id})
        msg = ws.receive_json()
        assert msg["op"] == "error"

    columns = client.get(f"/api/kanban/{other_id}").json()["columns"]
    assert columns[0]["id"] == other_column_id
    assert columns[0]["title"] != "hacked"


def test_websocket_rejects_card_for_unknown_column(client, kanban_id):
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "create_card", "column_id": 999_999, "text": "x"})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_card_text_over_max_length(client, kanban_id):
    column_id = _first_column_id(client, kanban_id)
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "create_card", "column_id": column_id, "text": "A" * 501})
        msg = ws.receive_json()
    assert msg["op"] == "error"


@pytest.mark.parametrize("bad_color", ["red", "#fff", "#gggggg", "rgb(0,0,0)", "#12345", "<script>"])
def test_websocket_rejects_invalid_card_color_format(client, kanban_id, bad_color):
    column_id = _first_column_id(client, kanban_id)
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "create_card", "column_id": column_id, "text": "x", "color": bad_color})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_rejects_cards_beyond_max_per_column(client, kanban_id):
    column_id = _first_column_id(client, kanban_id)
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        for _ in range(200):
            ws.send_json({"op": "create_card", "column_id": column_id, "text": "x"})
            assert ws.receive_json()["op"] == "card_created"

        ws.send_json({"op": "create_card", "column_id": column_id, "text": "one too many"})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_update_nonexistent_card_returns_error(client, kanban_id):
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "update_card", "id": 999_999, "text": "x"})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_delete_nonexistent_card_returns_error(client, kanban_id):
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "delete_card", "id": 999_999})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_move_nonexistent_card_returns_error(client, kanban_id):
    column_id = _first_column_id(client, kanban_id)
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "move_card", "id": 999_999, "column_id": column_id, "index": 0})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_move_card_to_unknown_column_returns_error(client, kanban_id):
    column_id = _first_column_id(client, kanban_id)
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "create_card", "column_id": column_id, "text": "x"})
        card_id = ws.receive_json()["card"]["id"]

        ws.send_json({"op": "move_card", "id": card_id, "column_id": 999_999, "index": 0})
        msg = ws.receive_json()
    assert msg["op"] == "error"


def test_websocket_card_ops_reject_card_from_another_board(client, kanban_id):
    other_resp = client.post("/kanban/new", follow_redirects=False)
    other_id = other_resp.headers["location"].rsplit("/", 1)[-1]
    other_column_id = _first_column_id(client, other_id)

    with client.websocket_connect(f"/ws/kanban/{other_id}") as ws_a:
        ws_a.send_json({"op": "create_card", "column_id": other_column_id, "text": "real"})
        card_id = ws_a.receive_json()["card"]["id"]

    own_column_id = _first_column_id(client, kanban_id)
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws_b:
        ws_b.send_json({"op": "update_card", "id": card_id, "text": "hacked"})
        assert ws_b.receive_json()["op"] == "error"

        ws_b.send_json({"op": "move_card", "id": card_id, "column_id": own_column_id, "index": 0})
        assert ws_b.receive_json()["op"] == "error"

        ws_b.send_json({"op": "delete_card", "id": card_id})
        assert ws_b.receive_json()["op"] == "error"

    cards = client.get(f"/api/kanban/{other_id}").json()["columns"][0]["cards"]
    assert len(cards) == 1
    assert cards[0]["text"] == "real"


def test_websocket_card_text_is_never_reflected_as_html(client, kanban_id):
    xss = "<img src=x onerror=alert(1)>"
    column_id = _first_column_id(client, kanban_id)
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "create_card", "column_id": column_id, "text": xss})
        msg = ws.receive_json()
    assert msg["op"] == "card_created"
    assert msg["card"]["text"] == xss  # stored as plain text, never HTML

    page = client.get(f"/kanban/{kanban_id}")
    assert xss not in page.text


def test_websocket_column_title_is_never_reflected_as_html(client, kanban_id):
    xss = "<img src=x onerror=alert(1)>"
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "create_column", "title": xss})
        msg = ws.receive_json()
    assert msg["op"] == "column_created"
    assert msg["column"]["title"] == xss

    page = client.get(f"/kanban/{kanban_id}")
    assert xss not in page.text


def test_websocket_ignores_unexpected_extra_fields(client, kanban_id):
    column_id = _first_column_id(client, kanban_id)
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json(
            {
                "op": "create_card",
                "column_id": column_id,
                "text": "hi",
                "onclick": "alert(1)",
                "__proto__": "x",
            }
        )
        msg = ws.receive_json()
    assert msg["op"] == "card_created"
    assert "onclick" not in msg["card"]
    assert "__proto__" not in msg["card"]
