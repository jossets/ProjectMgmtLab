def test_home_page_lists_new_kanban_button(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Nouveau Kanban" in resp.text


def test_create_kanban_redirects_to_new_id(client):
    resp = client.post("/kanban/new", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/kanban/")


def test_kanban_page_loads(client, kanban_id):
    resp = client.get(f"/kanban/{kanban_id}")
    assert resp.status_code == 200
    assert "Nouveau Kanban" in resp.text


def test_new_kanban_starts_with_three_default_columns(client, kanban_id):
    resp = client.get(f"/api/kanban/{kanban_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Nouveau Kanban"
    assert [c["title"] for c in data["columns"]] == ["À faire", "En cours", "Terminé"]
    assert all(c["cards"] == [] for c in data["columns"])


def test_unknown_but_well_formed_kanban_id_returns_404(client):
    assert client.get("/kanban/" + "a" * 32).status_code == 404
    assert client.get("/api/kanban/" + "b" * 32).status_code == 404


def test_rename_kanban(client, kanban_id):
    resp = client.patch(f"/api/kanban/{kanban_id}", json={"name": "Sprint 1"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Sprint 1"
    assert client.get(f"/api/kanban/{kanban_id}").json()["name"] == "Sprint 1"


def _first_column_id(client, kanban_id):
    return client.get(f"/api/kanban/{kanban_id}").json()["columns"][0]["id"]


def test_websocket_create_column(client, kanban_id):
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "create_column", "title": "Idées", "client_ref": "ref1"})
        msg = ws.receive_json()

    assert msg["op"] == "column_created"
    assert msg["column"]["title"] == "Idées"
    assert msg["column"]["order_index"] == 3
    assert msg["client_ref"] == "ref1"

    columns = client.get(f"/api/kanban/{kanban_id}").json()["columns"]
    assert [c["title"] for c in columns] == ["À faire", "En cours", "Terminé", "Idées"]


def test_websocket_rename_column(client, kanban_id):
    column_id = _first_column_id(client, kanban_id)
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "rename_column", "id": column_id, "title": "Backlog"})
        msg = ws.receive_json()

    assert msg == {"op": "column_renamed", "id": column_id, "title": "Backlog"}
    columns = client.get(f"/api/kanban/{kanban_id}").json()["columns"]
    assert columns[0]["title"] == "Backlog"


def test_websocket_delete_column_cascades_cards(client, kanban_id):
    column_id = _first_column_id(client, kanban_id)
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "create_card", "column_id": column_id, "text": "tâche"})
        ws.receive_json()

        ws.send_json({"op": "delete_column", "id": column_id})
        msg = ws.receive_json()

    assert msg == {"op": "column_deleted", "id": column_id}
    columns = client.get(f"/api/kanban/{kanban_id}").json()["columns"]
    assert [c["id"] for c in columns] == [c["id"] for c in columns if c["id"] != column_id]
    assert len(columns) == 2


def test_websocket_move_column(client, kanban_id):
    columns = client.get(f"/api/kanban/{kanban_id}").json()["columns"]
    first_id = columns[0]["id"]

    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "move_column", "id": first_id, "index": 2})
        msg = ws.receive_json()

    assert msg["op"] == "columns_reordered"
    reordered = client.get(f"/api/kanban/{kanban_id}").json()["columns"]
    assert [c["id"] for c in reordered] == [columns[1]["id"], columns[2]["id"], first_id]
    assert [c["order_index"] for c in reordered] == [0, 1, 2]


def test_websocket_create_update_delete_card_cycle(client, kanban_id):
    column_id = _first_column_id(client, kanban_id)
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json(
            {"op": "create_card", "column_id": column_id, "text": "écrire les specs", "color": "#ffd43b", "client_ref": "cref"}
        )
        created = ws.receive_json()
        assert created["op"] == "card_created"
        assert created["card"]["text"] == "écrire les specs"
        assert created["card"]["color"] == "#ffd43b"
        assert created["client_ref"] == "cref"
        card_id = created["card"]["id"]

        ws.send_json({"op": "update_card", "id": card_id, "text": "specs écrites", "color": "#69db7c"})
        updated = ws.receive_json()
        assert updated["op"] == "card_updated"
        assert updated["card"]["text"] == "specs écrites"
        assert updated["card"]["color"] == "#69db7c"

        ws.send_json({"op": "delete_card", "id": card_id})
        deleted = ws.receive_json()
        assert deleted == {"op": "card_deleted", "id": card_id, "column_id": column_id}

    columns = client.get(f"/api/kanban/{kanban_id}").json()["columns"]
    assert all(c["cards"] == [] for c in columns)


def test_websocket_move_card_within_same_column_reorders(client, kanban_id):
    column_id = _first_column_id(client, kanban_id)
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ids = []
        for text in ["a", "b", "c"]:
            ws.send_json({"op": "create_card", "column_id": column_id, "text": text})
            ids.append(ws.receive_json()["card"]["id"])

        ws.send_json({"op": "move_card", "id": ids[0], "column_id": column_id, "index": 2})
        msg = ws.receive_json()

    assert msg["op"] == "card_moved"
    assert msg["columns"] == {str(column_id): [ids[1], ids[2], ids[0]]}

    cards = client.get(f"/api/kanban/{kanban_id}").json()["columns"][0]["cards"]
    assert [c["id"] for c in cards] == [ids[1], ids[2], ids[0]]
    assert [c["order_index"] for c in cards] == [0, 1, 2]


def test_websocket_move_card_across_columns(client, kanban_id):
    columns = client.get(f"/api/kanban/{kanban_id}").json()["columns"]
    col_a, col_b = columns[0]["id"], columns[1]["id"]

    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "create_card", "column_id": col_a, "text": "carte"})
        card_id = ws.receive_json()["card"]["id"]

        ws.send_json({"op": "move_card", "id": card_id, "column_id": col_b, "index": 0})
        msg = ws.receive_json()

    assert msg["op"] == "card_moved"
    assert msg["columns"] == {str(col_a): [], str(col_b): [card_id]}

    columns = client.get(f"/api/kanban/{kanban_id}").json()["columns"]
    assert columns[0]["cards"] == []
    assert [c["id"] for c in columns[1]["cards"]] == [card_id]
    assert columns[1]["cards"][0]["column_id"] == col_b


def test_websocket_broadcasts_to_other_connected_clients(client, kanban_id):
    column_id = _first_column_id(client, kanban_id)
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws1:
        with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws2:
            ws1.send_json({"op": "create_card", "column_id": column_id, "text": "hi"})
            msg1 = ws1.receive_json()
            msg2 = ws2.receive_json()
            assert msg1 == msg2


def test_websocket_does_not_hold_a_db_connection_while_idle(client, kanban_id):
    # regression guard — see the matching test in test_functional_whiteboard.py
    from app.db import engine

    column_id = _first_column_id(client, kanban_id)
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        assert engine.pool.checkedout() == 0
        ws.send_json({"op": "create_card", "column_id": column_id, "text": "hi"})
        ws.receive_json()
        assert engine.pool.checkedout() == 0
