def test_home_page_lists_new_gantt_button(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Nouveau Gantt" in resp.text


def test_create_gantt_redirects_to_new_id(client):
    resp = client.post("/gantt/new", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/gantt/")


def test_unknown_but_well_formed_gantt_id_returns_404_on_page(client):
    resp = client.get("/gantt/" + "a" * 32)
    assert resp.status_code == 404


def test_unknown_but_well_formed_gantt_id_returns_404_on_api(client):
    resp = client.get("/api/gantt/" + "b" * 32)
    assert resp.status_code == 404


def test_new_gantt_starts_empty(client, gantt_id):
    resp = client.get(f"/api/gantt/{gantt_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Nouveau Gantt"
    assert data["tasks"] == []


def test_save_and_reload_tasks_round_trip(client, gantt_id):
    payload = [
        {
            "order_index": 0,
            "indent_level": 0,
            "name": "Phase 1",
            "start_date": "2026-08-01",
            "end_date": "2026-08-10",
            "progress_pct": 40,
        },
        {
            "order_index": 1,
            "indent_level": 1,
            "name": "Sous-tâche",
            "start_date": "2026-08-02",
            "end_date": "2026-08-05",
            "progress_pct": 20,
        },
    ]
    put_resp = client.put(f"/api/gantt/{gantt_id}/tasks", json=payload)
    assert put_resp.status_code == 200
    assert put_resp.json()["ok"] is True

    tasks = client.get(f"/api/gantt/{gantt_id}").json()["tasks"]
    assert len(tasks) == 2
    assert tasks[0]["name"] == "Phase 1"
    assert tasks[0]["start_date"] == "2026-08-01"
    assert tasks[0]["end_date"] == "2026-08-10"
    assert tasks[0]["progress_pct"] == 40
    assert tasks[1]["indent_level"] == 1


def test_replace_tasks_overwrites_previous_set(client, gantt_id):
    first = [{"order_index": 0, "name": "A"}]
    second = [{"order_index": 0, "name": "B"}]
    client.put(f"/api/gantt/{gantt_id}/tasks", json=first)
    client.put(f"/api/gantt/{gantt_id}/tasks", json=second)

    tasks = client.get(f"/api/gantt/{gantt_id}").json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["name"] == "B"


def test_task_defaults_when_optional_fields_omitted(client, gantt_id):
    payload = [{"order_index": 0}]
    client.put(f"/api/gantt/{gantt_id}/tasks", json=payload)
    task = client.get(f"/api/gantt/{gantt_id}").json()["tasks"][0]
    assert task["indent_level"] == 0
    assert task["name"] == ""
    assert task["progress_pct"] == 0
    assert task["start_date"] is None
    assert task["end_date"] is None


def test_rename_gantt(client, gantt_id):
    resp = client.patch(f"/api/gantt/{gantt_id}", json={"name": "Cours Gantt Demo"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Cours Gantt Demo"
    assert client.get(f"/api/gantt/{gantt_id}").json()["name"] == "Cours Gantt Demo"


def test_rename_with_blank_name_keeps_previous_name(client, gantt_id):
    client.patch(f"/api/gantt/{gantt_id}", json={"name": "Kept Name"})
    resp = client.patch(f"/api/gantt/{gantt_id}", json={"name": "   "})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Kept Name"


def test_logged_in_user_sees_profile_link_on_gantt_page(teacher_client, gantt_id):
    page = teacher_client.get(f"/gantt/{gantt_id}")
    assert page.status_code == 200
    assert 'href="/profile"' in page.text
