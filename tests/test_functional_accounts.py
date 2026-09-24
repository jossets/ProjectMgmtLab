import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import User


def _uname(prefix="user"):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _user_id(username):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.username == username).first().id
    finally:
        db.close()


def test_home_page_shows_login_link_when_logged_out(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Connexion" in resp.text


def test_admin_login_redirects_and_sets_session(client):
    resp = client.post("/login", data={"username": "testadmin", "password": "S3cur3-Test-Pass!"}, follow_redirects=False)
    assert resp.status_code == 303

    home = client.get("/")
    assert "Comptes" in home.text
    assert "admin connecté" in home.text


def test_home_page_lists_student_sessions(student_client, teacher_client):
    resp = teacher_client.post("/sessions/new", follow_redirects=False)
    session_id = resp.headers["location"].rsplit("/", 1)[-1]
    student_client.post(f"/join/{session_id}")

    home = student_client.get("/")
    assert resp.status_code == 303  # sanity: session really was created
    assert f'/sessions/{session_id}' in home.text
    assert "Mes sessions" in home.text


def test_home_page_hides_sessions_card_for_teacher_and_anonymous(teacher_client, client):
    assert "Mes sessions" not in teacher_client.get("/").text
    assert "Mes sessions" not in client.get("/").text


def test_home_page_shows_empty_state_for_student_with_no_sessions(student_client):
    home = student_client.get("/")
    assert "n'avez rejoint aucune session" in home.text


def test_accounts_page_requires_teacher(client):
    resp = client.get("/accounts", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_admin_can_view_accounts_page(admin_client):
    resp = admin_client.get("/accounts")
    assert resp.status_code == 200
    assert "Enseignants" in resp.text
    assert "Élèves" in resp.text


def test_admin_creates_teacher_and_teacher_appears_in_list(admin_client):
    username = _uname("newteacher")
    resp = admin_client.post("/accounts/teachers", data={"username": username, "password": "Sup3rSecret!"}, follow_redirects=False)
    assert resp.status_code == 303

    page = admin_client.get("/accounts")
    assert username in page.text


def test_newly_created_teacher_can_log_in(admin_client):
    username = _uname("logmein")
    admin_client.post("/accounts/teachers", data={"username": username, "password": "Sup3rSecret!"})

    fresh = TestClient(app)
    resp = fresh.post("/login", data={"username": username, "password": "Sup3rSecret!"}, follow_redirects=False)
    assert resp.status_code == 303
    home = fresh.get("/")
    assert "Comptes" in home.text  # teacher role also sees the accounts nav link


def test_teacher_can_also_create_a_teacher(teacher_client):
    username = _uname("secondgen")
    resp = teacher_client.post("/accounts/teachers", data={"username": username, "password": "Sup3rSecret!"}, follow_redirects=False)
    assert resp.status_code == 303

    page = teacher_client.get("/accounts")
    assert username in page.text


def test_block_then_unblock_teacher_account(admin_client):
    username = _uname("toblock")
    admin_client.post("/accounts/teachers", data={"username": username, "password": "Sup3rSecret!"})
    target_id = _user_id(username)

    blocked = admin_client.post(f"/accounts/{target_id}/block", follow_redirects=False)
    assert blocked.status_code == 303

    denied = TestClient(app).post("/login", data={"username": username, "password": "Sup3rSecret!"}, follow_redirects=False)
    assert denied.status_code == 401

    unblocked = admin_client.post(f"/accounts/{target_id}/unblock", follow_redirects=False)
    assert unblocked.status_code == 303

    allowed_again = TestClient(app).post("/login", data={"username": username, "password": "Sup3rSecret!"}, follow_redirects=False)
    assert allowed_again.status_code == 303


def test_logout_clears_session(admin_client):
    admin_client.post("/logout")
    resp = admin_client.get("/accounts", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_create_account_defaults_to_teacher_role(admin_client):
    username = _uname("defaultrole")
    admin_client.post("/accounts/teachers", data={"username": username, "password": "Sup3rSecret!"})
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        assert user.role == "teacher"
    finally:
        db.close()


def test_teacher_can_create_a_student_account_from_accounts_page(admin_client):
    username = _uname("madestudent")
    resp = admin_client.post(
        "/accounts/teachers", data={"username": username, "password": "Sup3rSecret!", "role": "student"}, follow_redirects=False
    )
    assert resp.status_code == 303

    page = admin_client.get("/accounts")
    assert username in page.text
    # appears under Élèves, not Enseignants
    students_section = page.text.split("Élèves")[1]
    assert username in students_section

    fresh = TestClient(app)
    login = fresh.post("/login", data={"username": username, "password": "Sup3rSecret!"}, follow_redirects=False)
    assert login.status_code == 303
    home = fresh.get("/")
    assert "Mon profil" in home.text  # student nav, not the teacher "Comptes"/"Sessions" links
