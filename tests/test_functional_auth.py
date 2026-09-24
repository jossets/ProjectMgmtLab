from app.config import ADMIN_PASSWORD, ADMIN_USERNAME


def test_login_page_loads(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "Connexion admin" in resp.text


def test_home_shows_logged_out_state_by_default(client):
    resp = client.get("/")
    assert "Connexion" in resp.text
    assert "admin connecté" not in resp.text


def test_login_with_valid_credentials_sets_session(client):
    resp = client.post(
        "/login",
        data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"

    home = client.get("/")
    assert "admin connecté" in home.text


def test_login_with_invalid_credentials_rejected(client):
    resp = client.post("/login", data={"username": ADMIN_USERNAME, "password": "wrong"})
    assert resp.status_code == 401
    assert "Identifiants invalides" in resp.text


def test_logout_clears_session(client):
    client.post(
        "/login",
        data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        follow_redirects=False,
    )
    resp = client.post("/logout", follow_redirects=False)
    assert resp.status_code == 303

    home = client.get("/")
    assert "Connexion" in home.text
