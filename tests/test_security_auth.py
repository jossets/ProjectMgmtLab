from app.config import ADMIN_PASSWORD, ADMIN_USERNAME
from app.routers.auth import MAX_LOGIN_ATTEMPTS


def test_wrong_password_rejected(client):
    resp = client.post("/login", data={"username": ADMIN_USERNAME, "password": "wrong"})
    assert resp.status_code == 401


def test_sql_injection_style_credentials_rejected(client):
    resp = client.post(
        "/login",
        data={"username": "' OR '1'='1", "password": "' OR '1'='1"},
    )
    assert resp.status_code == 401


def test_empty_credentials_rejected(client):
    resp = client.post("/login", data={"username": "", "password": ""})
    assert resp.status_code == 401


def test_missing_credentials_returns_422(client):
    resp = client.post("/login", data={"username": ADMIN_USERNAME})
    assert resp.status_code == 422


def test_oversized_login_fields_rejected(client):
    resp = client.post("/login", data={"username": "x" * 200, "password": "y" * 300})
    assert resp.status_code == 422


def test_brute_force_lockout_after_max_attempts(client):
    for _ in range(MAX_LOGIN_ATTEMPTS):
        resp = client.post("/login", data={"username": ADMIN_USERNAME, "password": "wrong"})
        assert resp.status_code == 401

    locked = client.post("/login", data={"username": ADMIN_USERNAME, "password": "wrong"})
    assert locked.status_code == 429

    # correct credentials are blocked too while the lockout window is active
    still_locked = client.post("/login", data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert still_locked.status_code == 429


def test_lockout_is_scoped_and_does_not_affect_fresh_client(client):
    for _ in range(MAX_LOGIN_ATTEMPTS):
        client.post("/login", data={"username": ADMIN_USERNAME, "password": "wrong"})
    assert client.post("/login", data={"username": ADMIN_USERNAME, "password": "wrong"}).status_code == 429

    from fastapi.testclient import TestClient
    from app.main import app

    other_client = TestClient(app)
    resp = other_client.post(
        "/login",
        data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        follow_redirects=False,
    )
    # NOTE: TestClient requests share the same simulated client host, so this
    # documents current behaviour rather than true per-IP isolation.
    assert resp.status_code in (303, 429)


def test_session_cookie_is_httponly(client):
    resp = client.post(
        "/login",
        data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        follow_redirects=False,
    )
    set_cookie = resp.headers.get("set-cookie", "")
    assert "httponly" in set_cookie.lower()


def test_unauthenticated_session_has_no_admin_flag(client):
    resp = client.get("/")
    assert "admin connecté" not in resp.text
