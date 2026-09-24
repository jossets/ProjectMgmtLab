def _sample_element(**overrides):
    element = {"type": "text", "x": 0, "y": 0, "width": 100, "height": 50, "z_index": 0, "data": {"content": "hi"}}
    element.update(overrides)
    return element


# --- Gantt ---------------------------------------------------------------


def test_gantt_history_starts_empty(client, gantt_id):
    resp = client.get(f"/api/gantt/{gantt_id}/history")
    assert resp.status_code == 200
    assert resp.json() == {"entries": []}


def test_gantt_task_create_update_delete_are_logged(client, gantt_id):
    create_payload = [{"client_ref": "r1", "order_index": 0, "name": "Phase 1", "progress_pct": 0}]
    client.put(f"/api/gantt/{gantt_id}/tasks", json=create_payload)

    entries = client.get(f"/api/gantt/{gantt_id}/history").json()["entries"]
    assert len(entries) == 1
    assert entries[0]["action"] == "created"
    assert "Phase 1" in entries[0]["summary"]
    assert entries[0]["actor_label"] == "Anonyme"

    task = client.get(f"/api/gantt/{gantt_id}").json()["tasks"][0]

    # update the task's progress, matching it back up by id — this must be
    # treated as an update, not a delete+create
    update_payload = [{"id": task["id"], "order_index": 0, "name": "Phase 1", "progress_pct": 50}]
    client.put(f"/api/gantt/{gantt_id}/tasks", json=update_payload)

    entries = client.get(f"/api/gantt/{gantt_id}/history").json()["entries"]
    assert entries[0]["action"] == "updated"
    assert "50%" in entries[0]["summary"]

    # delete: send an empty task list
    client.put(f"/api/gantt/{gantt_id}/tasks", json=[])
    entries = client.get(f"/api/gantt/{gantt_id}/history").json()["entries"]
    assert entries[0]["action"] == "deleted"
    assert "Phase 1" in entries[0]["summary"]

    # 1 create + 1 update + 1 delete, newest first
    assert [e["action"] for e in entries] == ["deleted", "updated", "created"]


def test_gantt_resend_of_unchanged_task_does_not_log_an_update(client, gantt_id):
    payload = [{"client_ref": "r1", "order_index": 0, "name": "Stable", "progress_pct": 10}]
    client.put(f"/api/gantt/{gantt_id}/tasks", json=payload)
    task = client.get(f"/api/gantt/{gantt_id}").json()["tasks"][0]

    # resave the exact same task (same id, same fields) — a debounced
    # autosave with nothing changed must not spam the log
    same_payload = [{"id": task["id"], "order_index": 0, "name": "Stable", "progress_pct": 10}]
    client.put(f"/api/gantt/{gantt_id}/tasks", json=same_payload)

    entries = client.get(f"/api/gantt/{gantt_id}/history").json()["entries"]
    assert len(entries) == 1
    assert entries[0]["action"] == "created"


def test_gantt_task_id_from_another_gantt_cannot_be_edited(client, gantt_id):
    other_id = client.post("/gantt/new", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    client.put(f"/api/gantt/{other_id}/tasks", json=[{"client_ref": "r1", "order_index": 0, "name": "Other's task"}])
    other_task_id = client.get(f"/api/gantt/{other_id}").json()["tasks"][0]["id"]

    # try to "update" the other gantt's task by id from this gantt
    client.put(f"/api/gantt/{gantt_id}/tasks", json=[{"id": other_task_id, "order_index": 0, "name": "Hijacked"}])

    # the other gantt's task is untouched
    assert client.get(f"/api/gantt/{other_id}").json()["tasks"][0]["name"] == "Other's task"
    # this gantt got a *new* task instead (created, not updated)
    entries = client.get(f"/api/gantt/{gantt_id}/history").json()["entries"]
    assert entries[0]["action"] == "created"


def test_gantt_rename_is_logged_only_when_name_changes(client, gantt_id):
    client.patch(f"/api/gantt/{gantt_id}", json={"name": "New Name"})
    client.patch(f"/api/gantt/{gantt_id}", json={"name": "New Name"})  # no-op resend

    entries = client.get(f"/api/gantt/{gantt_id}/history").json()["entries"]
    assert len(entries) == 1
    assert entries[0]["action"] == "renamed"


def test_gantt_history_reflects_authenticated_actor(teacher_client):
    resp = teacher_client.post("/gantt/new", follow_redirects=False)
    gantt_id = resp.headers["location"].rsplit("/", 1)[-1]
    teacher_client.patch(f"/api/gantt/{gantt_id}", json={"name": "Renamed"})

    entries = teacher_client.get(f"/api/gantt/{gantt_id}/history").json()["entries"]
    assert entries[0]["actor_label"] not in ("Anonyme", "Admin")


def test_gantt_history_reflects_admin_actor(admin_client, gantt_id):
    admin_client.patch(f"/api/gantt/{gantt_id}", json={"name": "Renamed by admin"})
    entries = admin_client.get(f"/api/gantt/{gantt_id}/history").json()["entries"]
    assert entries[0]["actor_label"] == "Admin"


# --- Whiteboard ------------------------------------------------------------


def test_whiteboard_ws_create_update_delete_are_logged(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        element_id = ws.receive_json()["element"]["id"]

        ws.send_json({"op": "update", "id": element_id, "element": _sample_element(x=10)})
        ws.receive_json()

        ws.send_json({"op": "delete", "id": element_id})
        ws.receive_json()

    entries = client.get(f"/api/whiteboard/{whiteboard_id}/history").json()["entries"]
    assert [e["action"] for e in entries] == ["deleted", "updated", "created"]
    assert all("texte" in e["summary"] for e in entries)


def test_whiteboard_reaction_toggle_is_not_logged(client, whiteboard_id):
    with client.websocket_connect(f"/ws/whiteboard/{whiteboard_id}") as ws:
        ws.send_json({"op": "create", "element": _sample_element()})
        element_id = ws.receive_json()["element"]["id"]

        ws.send_json({"op": "react", "id": element_id, "reaction": "heart", "voter_id": "a" * 32})
        ws.receive_json()

    entries = client.get(f"/api/whiteboard/{whiteboard_id}/history").json()["entries"]
    assert [e["action"] for e in entries] == ["created"]


def test_whiteboard_rename_is_logged_only_when_name_changes(client, whiteboard_id):
    client.patch(f"/api/whiteboard/{whiteboard_id}", json={"name": "Same"})
    client.patch(f"/api/whiteboard/{whiteboard_id}", json={"name": "Same"})
    entries = client.get(f"/api/whiteboard/{whiteboard_id}/history").json()["entries"]
    assert len(entries) == 1
    assert entries[0]["action"] == "renamed"


# --- Kanban ------------------------------------------------------------


def test_kanban_column_and_card_lifecycle_is_logged(client, kanban_id):
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "create_column", "title": "Idées", "client_ref": "c1"})
        column_id = ws.receive_json()["column"]["id"]

        ws.send_json({"op": "rename_column", "id": column_id, "title": "Backlog"})
        ws.receive_json()

        ws.send_json({"op": "create_card", "column_id": column_id, "text": "tâche 1", "client_ref": "r1"})
        card_id = ws.receive_json()["card"]["id"]

        ws.send_json({"op": "update_card", "id": card_id, "text": "tâche 1 modifiée", "color": "#fff3bf"})
        ws.receive_json()

        ws.send_json({"op": "delete_card", "id": card_id})
        ws.receive_json()

        ws.send_json({"op": "delete_column", "id": column_id})
        ws.receive_json()

    entries = client.get(f"/api/kanban/{kanban_id}/history").json()["entries"]
    actions = [e["action"] for e in entries]
    assert actions == ["deleted", "deleted", "updated", "created", "updated", "created"]
    assert "Backlog" in entries[0]["summary"]  # column deleted, renamed title


def test_kanban_move_card_within_same_column_is_not_logged(client, kanban_id):
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "create_column", "title": "A", "client_ref": "c1"})
        column_id = ws.receive_json()["column"]["id"]
        card_ids = []
        for text in ["1", "2", "3"]:
            ws.send_json({"op": "create_card", "column_id": column_id, "text": text})
            card_ids.append(ws.receive_json()["card"]["id"])

        ws.send_json({"op": "move_card", "id": card_ids[0], "column_id": column_id, "index": 2})
        ws.receive_json()

    entries = client.get(f"/api/kanban/{kanban_id}/history").json()["entries"]
    assert all(e["action"] != "updated" or "déplacée" not in e["summary"] for e in entries)


def test_kanban_move_card_across_columns_is_logged(client, kanban_id):
    with client.websocket_connect(f"/ws/kanban/{kanban_id}") as ws:
        ws.send_json({"op": "create_column", "title": "A", "client_ref": "c1"})
        col_a = ws.receive_json()["column"]["id"]
        ws.send_json({"op": "create_column", "title": "B", "client_ref": "c2"})
        col_b = ws.receive_json()["column"]["id"]

        ws.send_json({"op": "create_card", "column_id": col_a, "text": "carte"})
        card_id = ws.receive_json()["card"]["id"]

        ws.send_json({"op": "move_card", "id": card_id, "column_id": col_b, "index": 0})
        ws.receive_json()

    entries = client.get(f"/api/kanban/{kanban_id}/history").json()["entries"]
    assert entries[0]["action"] == "updated"
    assert "A" in entries[0]["summary"] and "B" in entries[0]["summary"]


def test_kanban_rename_is_logged_only_when_name_changes(client, kanban_id):
    client.patch(f"/api/kanban/{kanban_id}", json={"name": "Same"})
    client.patch(f"/api/kanban/{kanban_id}", json={"name": "Same"})
    entries = client.get(f"/api/kanban/{kanban_id}/history").json()["entries"]
    assert len(entries) == 1
    assert entries[0]["action"] == "renamed"
