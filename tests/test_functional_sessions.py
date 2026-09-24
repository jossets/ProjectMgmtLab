import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import User
from app.security import hash_password


def _uname(prefix="user"):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _create_student():
    username = _uname("student")
    db = SessionLocal()
    try:
        user = User(username=username, password_hash=hash_password("StudentPass1"), role="student")
        db.add(user)
        db.commit()
        db.refresh(user)
        return user.id, username
    finally:
        db.close()


def _new_session_id(client):
    resp = client.post("/sessions/new", follow_redirects=False)
    assert resp.status_code == 303
    return resp.headers["location"].rsplit("/", 1)[-1]


def test_sessions_list_requires_teacher(client):
    resp = client.get("/sessions", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_teacher_creates_session_and_it_appears_in_list(teacher_client):
    session_id = _new_session_id(teacher_client)
    page = teacher_client.get("/sessions")
    assert session_id in page.text


def test_new_session_auto_creates_a_whiteboard(teacher_client):
    session_id = _new_session_id(teacher_client)
    detail = teacher_client.get(f"/sessions/{session_id}")
    assert detail.status_code == 200
    assert "/whiteboard/" in detail.text


def test_rename_session(teacher_client):
    session_id = _new_session_id(teacher_client)
    resp = teacher_client.post(f"/sessions/{session_id}/rename", data={"name": "Cours du mardi"}, follow_redirects=False)
    assert resp.status_code == 303
    page = teacher_client.get(f"/sessions/{session_id}")
    assert "Cours du mardi" in page.text


def test_toggle_active_flips_status(teacher_client):
    session_id = _new_session_id(teacher_client)
    page = teacher_client.get(f"/sessions/{session_id}")
    assert "Active" in page.text

    teacher_client.post(f"/sessions/{session_id}/toggle-active", follow_redirects=False)
    page2 = teacher_client.get(f"/sessions/{session_id}")
    assert "Bloquée" in page2.text

    teacher_client.post(f"/sessions/{session_id}/toggle-active", follow_redirects=False)
    page3 = teacher_client.get(f"/sessions/{session_id}")
    assert "Active" in page3.text


def test_toggle_signups_flips_status(teacher_client):
    session_id = _new_session_id(teacher_client)
    page = teacher_client.get(f"/sessions/{session_id}")
    assert "Autorisée" in page.text

    teacher_client.post(f"/sessions/{session_id}/toggle-signups", follow_redirects=False)
    page2 = teacher_client.get(f"/sessions/{session_id}")
    assert "Désactivée" in page2.text


def test_admin_can_create_and_view_any_session(admin_client):
    session_id = _new_session_id(admin_client)
    detail = admin_client.get(f"/sessions/{session_id}")
    assert detail.status_code == 200
    assert "admin" in detail.text


def test_add_and_remove_member(teacher_client):
    session_id = _new_session_id(teacher_client)
    student_id, student_username = _create_student()

    added = teacher_client.post(f"/sessions/{session_id}/members", data={"user_id": student_id}, follow_redirects=False)
    assert added.status_code == 303
    page = teacher_client.get(f"/sessions/{session_id}")
    assert student_username in page.text

    removed = teacher_client.post(f"/sessions/{session_id}/members/{student_id}/remove", follow_redirects=False)
    assert removed.status_code == 303
    page2 = teacher_client.get(f"/sessions/{session_id}")
    # the student is no longer listed as a member (removing the "Retirer"
    # form for them) even though they're still around for the app to
    # know about — they now reappear in the "add a student" dropdown instead
    assert f"/sessions/{session_id}/members/{student_id}/remove" not in page2.text


def test_unknown_but_well_formed_session_id_returns_404(teacher_client):
    resp = teacher_client.get("/sessions/ZZZZZZZ")
    assert resp.status_code == 404


def test_join_page_shows_login_and_signup_when_logged_out(client, teacher_client):
    session_id = _new_session_id(teacher_client)
    resp = client.get(f"/join/{session_id}")
    assert resp.status_code == 200
    assert "Se connecter" in resp.text
    assert "Créer mon compte" in resp.text


def test_join_page_hides_signup_when_disabled(client, teacher_client):
    session_id = _new_session_id(teacher_client)
    teacher_client.post(f"/sessions/{session_id}/toggle-signups")

    resp = client.get(f"/join/{session_id}")
    assert resp.status_code == 200
    assert "Créer mon compte" not in resp.text


def test_join_page_redirects_teacher_to_management_view(teacher_client):
    session_id = _new_session_id(teacher_client)
    resp = teacher_client.get(f"/join/{session_id}", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/sessions/{session_id}"


def test_student_creates_account_via_join_and_is_auto_logged_in(client, teacher_client):
    session_id = _new_session_id(teacher_client)
    username = _uname("selfjoin")

    resp = client.post(
        f"/join/{session_id}/create-account",
        data={"username": username, "password": "MyOwnPass1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/sessions/{session_id}"

    profile = client.get("/profile")
    assert session_id in profile.text


def test_logged_in_student_can_join_via_button(student_client, teacher_client):
    session_id = _new_session_id(teacher_client)

    join_page = student_client.get(f"/join/{session_id}")
    assert "Rejoindre cette session" in join_page.text

    resp = student_client.post(f"/join/{session_id}", follow_redirects=False)
    assert resp.status_code == 303

    profile = student_client.get("/profile")
    assert session_id in profile.text


def test_session_member_sees_read_only_view(student_client, teacher_client):
    session_id = _new_session_id(teacher_client)
    student_client.post(f"/join/{session_id}")

    page = student_client.get(f"/sessions/{session_id}")
    assert page.status_code == 200
    assert "Renommer" not in page.text
    assert "Ajouter un élève" not in page.text


def test_session_page_nav_has_no_teacher_only_links_for_a_student(student_client, teacher_client):
    # the /sessions list is teacher-only (require_teacher_page) — a student
    # following a "Sessions" link there would get bounced to /login
    session_id = _new_session_id(teacher_client)
    student_client.post(f"/join/{session_id}")

    page = student_client.get(f"/sessions/{session_id}")
    assert 'href="/sessions"' not in page.text
    assert 'href="/profile"' in page.text


def test_session_non_member_student_redirected_to_join(student_client, teacher_client):
    session_id = _new_session_id(teacher_client)
    resp = student_client.get(f"/sessions/{session_id}", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/join/{session_id}"


def test_profile_requires_login(client):
    resp = client.get("/profile", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_profile_lists_no_sessions_when_none_joined(student_client):
    resp = student_client.get("/profile")
    assert resp.status_code == 200
    assert "n'avez rejoint aucune session" in resp.text


def test_teacher_can_access_profile(teacher_client):
    resp = teacher_client.get("/profile")
    assert resp.status_code == 200
    assert "Mes sessions" not in resp.text  # sessions list is student-only


def test_admin_sees_profile_without_username_form(admin_client):
    resp = admin_client.get("/profile", follow_redirects=False)
    assert resp.status_code == 200
    assert "Compte administrateur" in resp.text
    assert 'name="username"' not in resp.text


def test_student_can_change_username(student_client):
    new_name = f"renamed_{uuid.uuid4().hex[:10]}"
    resp = student_client.post("/profile/username", data={"username": new_name}, follow_redirects=False)
    assert resp.status_code == 303
    page = student_client.get("/profile")
    assert new_name in page.text


def test_teacher_can_change_username_and_still_logged_in(teacher_client):
    new_name = f"renamed_{uuid.uuid4().hex[:10]}"
    resp = teacher_client.post("/profile/username", data={"username": new_name}, follow_redirects=False)
    assert resp.status_code == 303
    # session cookie is keyed by user id, not username — still logged in, and
    # teacher-only pages remain reachable under the new name
    assert teacher_client.get("/sessions").status_code == 200
    assert new_name in teacher_client.get("/profile").text


def test_username_change_rejects_duplicate(teacher_client, student_client):
    taken = student_client.get("/profile").text
    # pull the student's current username straight from their profile form
    m = re.search(r'name="username"\s+value="([^"]+)"', taken)
    student_username = m.group(1)

    resp = teacher_client.post("/profile/username", data={"username": student_username}, follow_redirects=False)
    assert resp.status_code == 409


def test_username_change_keeps_current_name_when_unchanged(student_client):
    before = re.search(r'name="username"\s+value="([^"]+)"', student_client.get("/profile").text).group(1)
    resp = student_client.post("/profile/username", data={"username": before}, follow_redirects=False)
    assert resp.status_code == 303
