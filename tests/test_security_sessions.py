import uuid

import pytest
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
        return user.id
    finally:
        db.close()


def _new_session_id(client):
    resp = client.post("/sessions/new", follow_redirects=False)
    assert resp.status_code == 303
    return resp.headers["location"].rsplit("/", 1)[-1]


def _other_teacher_client(admin_client):
    username = _uname("otherteacher")
    admin_client.post("/accounts/teachers", data={"username": username, "password": "Sup3rSecret!"}, follow_redirects=False)
    c = TestClient(app)
    resp = c.post("/login", data={"username": username, "password": "Sup3rSecret!"}, follow_redirects=False)
    assert resp.status_code == 303
    return c


def test_create_session_rejects_unauthenticated_caller(client):
    resp = client.post("/sessions/new")
    assert resp.status_code == 403


def test_session_detail_rejects_unauthenticated_caller_with_login_redirect(client, teacher_client):
    session_id = _new_session_id(teacher_client)
    resp = client.get(f"/sessions/{session_id}", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_session_detail_rejects_non_owner_teacher(admin_client, teacher_client):
    session_id = _new_session_id(teacher_client)
    intruder = _other_teacher_client(admin_client)
    resp = intruder.get(f"/sessions/{session_id}")
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "path_suffix",
    ["rename", "toggle-active", "toggle-signups"],
)
def test_mutation_endpoints_reject_non_owner_teacher(admin_client, teacher_client, path_suffix):
    session_id = _new_session_id(teacher_client)
    intruder = _other_teacher_client(admin_client)
    resp = intruder.post(f"/sessions/{session_id}/{path_suffix}", data={"name": "hacked"})
    assert resp.status_code == 403

    page = teacher_client.get(f"/sessions/{session_id}")
    assert "hacked" not in page.text


def test_add_member_rejects_non_owner_teacher(admin_client, teacher_client):
    session_id = _new_session_id(teacher_client)
    intruder = _other_teacher_client(admin_client)
    student_id = _create_student()

    resp = intruder.post(f"/sessions/{session_id}/members", data={"user_id": student_id})
    assert resp.status_code == 403


def test_remove_member_rejects_non_owner_teacher(admin_client, teacher_client):
    session_id = _new_session_id(teacher_client)
    student_id = _create_student()
    teacher_client.post(f"/sessions/{session_id}/members", data={"user_id": student_id})

    intruder = _other_teacher_client(admin_client)
    resp = intruder.post(f"/sessions/{session_id}/members/{student_id}/remove")
    assert resp.status_code == 403

    page = teacher_client.get(f"/sessions/{session_id}")
    assert "Retirer" in page.text  # membership survived the rejected attempt


@pytest.mark.parametrize("bad_id", ["short", "toolongforthis", "abcdefg", "3d6gh75", "3D6-H75"])
def test_session_detail_rejects_malformed_id(teacher_client, bad_id):
    resp = teacher_client.get(f"/sessions/{bad_id}")
    assert resp.status_code == 422


def test_add_member_rejects_non_student_user(teacher_client, admin_client):
    session_id = _new_session_id(teacher_client)
    other_teacher_username = _uname("notastudent")
    db = SessionLocal()
    try:
        teacher_user = User(username=other_teacher_username, password_hash=hash_password("x"), role="teacher")
        db.add(teacher_user)
        db.commit()
        teacher_id = teacher_user.id
    finally:
        db.close()

    resp = teacher_client.post(f"/sessions/{session_id}/members", data={"user_id": teacher_id})
    assert resp.status_code == 404


def test_add_member_rejects_malformed_user_id(teacher_client):
    session_id = _new_session_id(teacher_client)
    resp = teacher_client.post(f"/sessions/{session_id}/members", data={"user_id": "not-a-valid-id"})
    assert resp.status_code == 422


def test_add_member_twice_does_not_duplicate(teacher_client):
    session_id = _new_session_id(teacher_client)
    student_id = _create_student()

    first = teacher_client.post(f"/sessions/{session_id}/members", data={"user_id": student_id}, follow_redirects=False)
    assert first.status_code == 303
    second = teacher_client.post(f"/sessions/{session_id}/members", data={"user_id": student_id}, follow_redirects=False)
    assert second.status_code == 303  # idempotent, not a crash from the unique constraint

    page = teacher_client.get(f"/sessions/{session_id}")
    assert page.text.count("Retirer") == 1


def test_remove_member_rejects_malformed_user_id(teacher_client):
    session_id = _new_session_id(teacher_client)
    resp = teacher_client.post(f"/sessions/{session_id}/members/not-a-valid-id/remove")
    assert resp.status_code == 422


def test_join_page_rejects_unknown_session(client):
    resp = client.get("/join/ZZZZZZZ")
    assert resp.status_code == 404


@pytest.mark.parametrize("bad_id", ["short", "toolongforthis", "abcdefg", "3d6gh75"])
def test_join_page_rejects_malformed_session_id(client, bad_id):
    resp = client.get(f"/join/{bad_id}")
    assert resp.status_code == 422


def test_join_submit_rejects_unauthenticated_caller(client, teacher_client):
    session_id = _new_session_id(teacher_client)
    resp = client.post(f"/join/{session_id}")
    assert resp.status_code == 403


def test_join_submit_rejects_teacher_identity(admin_client, teacher_client):
    session_id = _new_session_id(teacher_client)
    intruder = _other_teacher_client(admin_client)
    resp = intruder.post(f"/join/{session_id}")
    assert resp.status_code == 403


def test_create_account_rejected_when_signups_disabled(client, teacher_client):
    session_id = _new_session_id(teacher_client)
    teacher_client.post(f"/sessions/{session_id}/toggle-signups")

    resp = client.post(
        f"/join/{session_id}/create-account", data={"username": _uname(), "password": "Sup3rSecret!"}
    )
    assert resp.status_code == 403


def test_create_account_rejects_duplicate_username(client, teacher_client):
    session_id = _new_session_id(teacher_client)
    username = _uname("dupjoin")

    first = client.post(
        f"/join/{session_id}/create-account",
        data={"username": username, "password": "Sup3rSecret!"},
        follow_redirects=False,
    )
    assert first.status_code == 303

    second_client = TestClient(app)
    second = second_client.post(
        f"/join/{session_id}/create-account", data={"username": username, "password": "AnotherPass1"}
    )
    assert second.status_code == 409


@pytest.mark.parametrize("bad_username", ["ab", "a" * 51, "with space", "<script>", ""])
def test_create_account_rejects_malformed_username(client, teacher_client, bad_username):
    session_id = _new_session_id(teacher_client)
    resp = client.post(
        f"/join/{session_id}/create-account", data={"username": bad_username, "password": "Sup3rSecret!"}
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("bad_password", ["short1", "", "a" * 201])
def test_create_account_rejects_malformed_password(client, teacher_client, bad_password):
    session_id = _new_session_id(teacher_client)
    resp = client.post(
        f"/join/{session_id}/create-account", data={"username": _uname(), "password": bad_password}
    )
    assert resp.status_code == 422


def test_create_account_rate_limited(teacher_client):
    session_id = _new_session_id(teacher_client)
    from app.routers.sessions import MAX_SIGNUP_ATTEMPTS

    fresh = TestClient(app)
    for _ in range(MAX_SIGNUP_ATTEMPTS):
        # well-formed requests: rate-limiting is checked inside the handler,
        # so a request that fails FastAPI's own Form validation (422) never
        # reaches that code and wouldn't count as an attempt
        fresh.post(
            f"/join/{session_id}/create-account",
            data={"username": _uname(), "password": "Sup3rSecret!"},
            follow_redirects=False,
        )

    blocked = fresh.post(
        f"/join/{session_id}/create-account", data={"username": _uname(), "password": "Sup3rSecret!"}, follow_redirects=False
    )
    assert blocked.status_code == 429


def test_password_never_appears_in_join_or_profile_pages(client, teacher_client):
    session_id = _new_session_id(teacher_client)
    resp = client.post(
        f"/join/{session_id}/create-account", data={"username": _uname("nopwleak"), "password": "Sup3rSecret!"}
    )
    assert "Sup3rSecret!" not in resp.text
    assert "pbkdf2" not in resp.text

    profile = client.get("/profile")
    assert "pbkdf2" not in profile.text


@pytest.mark.parametrize("bad_next", ["http://evil.example/", "//evil.example", "/\\evil.example"])
def test_login_next_param_rejects_open_redirect(client, bad_next):
    resp = client.post(
        "/login",
        data={"username": "testadmin", "password": "S3cur3-Test-Pass!", "next": bad_next},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_login_next_param_allows_safe_relative_path(client, teacher_client):
    session_id = _new_session_id(teacher_client)
    resp = client.post(
        "/login",
        data={"username": "testadmin", "password": "S3cur3-Test-Pass!", "next": f"/join/{session_id}"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/join/{session_id}"


def test_username_change_requires_login(client):
    resp = client.post("/profile/username", data={"username": "someone"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.parametrize("bad_username", ["", "ab", "a" * 51, "has spaces", "<script>alert(1)</script>", "a/b"])
def test_username_change_rejects_invalid_format(student_client, bad_username):
    resp = student_client.post("/profile/username", data={"username": bad_username}, follow_redirects=False)
    assert resp.status_code == 422


def test_username_change_does_not_let_student_escalate_role(student_client):
    # updating only the username field must never touch role/blocked/password
    resp = student_client.post("/profile/username", data={"username": "still_a_student"}, follow_redirects=False)
    assert resp.status_code == 303
    assert student_client.get("/sessions", follow_redirects=False).status_code == 303  # still not a teacher


def test_admin_cannot_change_username_no_db_row_to_update(admin_client):
    # the admin identity isn't backed by a `users` row (it's env-var based),
    # so the endpoint must reject this rather than error out
    resp = admin_client.post("/profile/username", data={"username": "newadminname"}, follow_redirects=False)
    assert resp.status_code == 403
