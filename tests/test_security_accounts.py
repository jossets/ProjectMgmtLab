import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import User
from app.routers.auth import MAX_LOGIN_ATTEMPTS


def _uname(prefix="user"):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _user_id(username):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.username == username).first().id
    finally:
        db.close()


def test_create_teacher_rejects_unauthenticated_caller(client):
    resp = client.post("/accounts/teachers", data={"username": _uname(), "password": "Sup3rSecret!"})
    assert resp.status_code == 403


def test_create_teacher_rejects_duplicate_username(admin_client):
    username = _uname("dup")
    first = admin_client.post(
        "/accounts/teachers", data={"username": username, "password": "Sup3rSecret!"}, follow_redirects=False
    )
    assert first.status_code == 303
    second = admin_client.post("/accounts/teachers", data={"username": username, "password": "AnotherPass1"})
    assert second.status_code == 409


@pytest.mark.parametrize("bad_username", ["ab", "a" * 51, "with space", "<script>", "user;drop", ""])
def test_create_teacher_rejects_malformed_username(admin_client, bad_username):
    resp = admin_client.post("/accounts/teachers", data={"username": bad_username, "password": "Sup3rSecret!"})
    assert resp.status_code == 422


@pytest.mark.parametrize("bad_password", ["short1", "", "a" * 201])
def test_create_teacher_rejects_malformed_password(admin_client, bad_password):
    resp = admin_client.post("/accounts/teachers", data={"username": _uname(), "password": bad_password})
    assert resp.status_code == 422


def test_password_hash_never_appears_in_accounts_page(admin_client):
    username = _uname("hashcheck")
    admin_client.post("/accounts/teachers", data={"username": username, "password": "Sup3rSecret!"})
    page = admin_client.get("/accounts").text
    assert "pbkdf2" not in page
    assert "Sup3rSecret!" not in page


def test_blocked_account_cannot_log_in_even_with_correct_password(admin_client):
    username = _uname("blockedlogin")
    admin_client.post("/accounts/teachers", data={"username": username, "password": "Sup3rSecret!"})
    target_id = _user_id(username)
    admin_client.post(f"/accounts/{target_id}/block")

    resp = TestClient(app).post("/login", data={"username": username, "password": "Sup3rSecret!"}, follow_redirects=False)
    assert resp.status_code == 401


def test_teacher_cannot_block_own_account(admin_client):
    username = _uname("selfblock")
    admin_client.post("/accounts/teachers", data={"username": username, "password": "Sup3rSecret!"})
    self_client = TestClient(app)
    self_client.post("/login", data={"username": username, "password": "Sup3rSecret!"})

    own_id = _user_id(username)
    resp = self_client.post(f"/accounts/{own_id}/block")
    assert resp.status_code == 400


def test_block_rejects_nonexistent_user_id(admin_client):
    resp = admin_client.post("/accounts/" + "a" * 32 + "/block")
    assert resp.status_code == 404


@pytest.mark.parametrize("bad_id", ["not-a-valid-id", "a" * 31, "a" * 33, "A" * 32, "'; DROP TABLE users; --"])
def test_block_rejects_malformed_user_id(admin_client, bad_id):
    resp = admin_client.post(f"/accounts/{bad_id}/block")
    assert resp.status_code == 422


def test_unauthenticated_cannot_block_accounts(client, admin_client):
    username = _uname("targetforblock")
    admin_client.post("/accounts/teachers", data={"username": username, "password": "Sup3rSecret!"})
    target_id = _user_id(username)

    resp = client.post(f"/accounts/{target_id}/block")
    assert resp.status_code == 403


def test_login_rate_limit_applies_to_db_account_path(admin_client):
    username = _uname("ratelimited")
    admin_client.post("/accounts/teachers", data={"username": username, "password": "Sup3rSecret!"})

    fresh = TestClient(app)
    for _ in range(MAX_LOGIN_ATTEMPTS):
        resp = fresh.post("/login", data={"username": username, "password": "wrong-password"})
        assert resp.status_code == 401

    blocked = fresh.post("/login", data={"username": username, "password": "Sup3rSecret!"})
    assert blocked.status_code == 429


@pytest.mark.parametrize("bad_role", ["admin", "TEACHER", "superuser", "teacher;drop table users"])
def test_create_account_rejects_invalid_role(admin_client, bad_role):
    resp = admin_client.post(
        "/accounts/teachers", data={"username": _uname(), "password": "Sup3rSecret!", "role": bad_role}
    )
    assert resp.status_code == 422


def test_create_student_account_rejected_when_unauthenticated(client):
    resp = client.post(
        "/accounts/teachers", data={"username": _uname(), "password": "Sup3rSecret!", "role": "student"}
    )
    assert resp.status_code == 403
