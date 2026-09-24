def _find_username(client):
    page = client.get("/profile").text
    import re

    m = re.search(r'name="username"\s+value="([^"]+)"', page)
    return m.group(1)


def test_ping_then_admin_sees_student_online(student_client, admin_client):
    username = _find_username(student_client)
    resp = student_client.post("/api/presence/ping", json={"page": "Tableau blanc", "visible": True})
    assert resp.status_code == 200

    data = admin_client.get("/api/presence/students").json()
    match = next(s for s in data["students"] if s["username"] == username)
    assert match["online"] is True
    assert match["page"] == "Tableau blanc"
    assert match["visible"] is True


def test_student_not_pinged_shows_offline(student_client, admin_client):
    data = admin_client.get("/api/presence/students").json()
    # student_client fixture logs in but never pings — must show offline
    assert all(not s["online"] for s in data["students"] if s["username"] == _find_username(student_client))


def test_gone_marks_student_offline_again(student_client, admin_client):
    student_client.post("/api/presence/ping", json={"page": "Kanban", "visible": True})
    username = _find_username(student_client)
    online_now = next(s for s in admin_client.get("/api/presence/students").json()["students"] if s["username"] == username)
    assert online_now["online"] is True

    student_client.post("/api/presence/gone")
    online_after = next(s for s in admin_client.get("/api/presence/students").json()["students"] if s["username"] == username)
    assert online_after["online"] is False


def test_ping_updates_page_on_subsequent_calls(student_client, admin_client):
    username = _find_username(student_client)
    student_client.post("/api/presence/ping", json={"page": "Gantt", "visible": True})
    student_client.post("/api/presence/ping", json={"page": "Cours", "visible": False})

    entry = next(s for s in admin_client.get("/api/presence/students").json()["students"] if s["username"] == username)
    assert entry["page"] == "Cours"
    assert entry["visible"] is False


def test_ping_from_anonymous_is_a_harmless_noop(client):
    resp = client.post("/api/presence/ping", json={"page": "Accueil", "visible": True})
    assert resp.status_code == 200


def test_gone_from_anonymous_is_a_harmless_noop(client):
    resp = client.post("/api/presence/gone")
    assert resp.status_code == 200


def test_teacher_ping_does_not_appear_in_student_list(teacher_client, admin_client):
    teacher_client.post("/api/presence/ping", json={"page": "Comptes", "visible": True})
    data = admin_client.get("/api/presence/students").json()
    usernames = {s["username"] for s in data["students"]}
    teacher_username = _find_username(teacher_client)
    assert teacher_username not in usernames
