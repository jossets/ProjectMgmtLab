import pytest


def test_students_list_requires_admin(client, teacher_client, student_client):
    for c in (client, teacher_client, student_client):
        resp = c.get("/api/presence/students", follow_redirects=False)
        assert resp.status_code == 403


@pytest.mark.parametrize("bad_page", ["x" * 61])
def test_ping_rejects_overlong_page_label(student_client, bad_page):
    resp = student_client.post("/api/presence/ping", json={"page": bad_page, "visible": True})
    assert resp.status_code == 422


def test_ping_rejects_missing_fields(student_client):
    resp = student_client.post("/api/presence/ping", json={"page": "Gantt"})
    assert resp.status_code == 422
    resp2 = student_client.post("/api/presence/ping", json={"visible": True})
    assert resp2.status_code == 422


def test_ping_rejects_wrong_types(student_client):
    resp = student_client.post("/api/presence/ping", json={"page": "Gantt", "visible": "maybe"})
    assert resp.status_code == 422
    resp2 = student_client.post("/api/presence/ping", json={"page": 12345, "visible": True})
    assert resp2.status_code == 422


def test_page_label_is_stored_verbatim_not_executed(student_client, admin_client):
    # never rendered as raw HTML anywhere server-side — this only proves the
    # value round-trips as plain data through the API, not that any
    # particular template is safe (the client always uses textContent)
    import re

    username = re.search(r'name="username"\s+value="([^"]+)"', student_client.get("/profile").text).group(1)

    xss = "<script>alert(1)</script>"
    student_client.post("/api/presence/ping", json={"page": xss, "visible": True})
    data = admin_client.get("/api/presence/students").json()
    match = next(s for s in data["students"] if s["username"] == username)
    assert match["page"] == xss
