import atexit
import base64
import os
import shutil
import tempfile
import uuid

_tmp_db_fd, _tmp_db_path = tempfile.mkstemp(suffix=".db")
os.close(_tmp_db_fd)
_tmp_data_dir = tempfile.mkdtemp(prefix="projectmgr-data-")
_tmp_courses_dir = tempfile.mkdtemp(prefix="projectmgr-cours-")


def _cleanup_tmp_db():
    try:
        os.remove(_tmp_db_path)
    except OSError:
        pass  # the SQLite engine may still hold the file open on Windows


def _cleanup_tmp_data_dir():
    shutil.rmtree(_tmp_data_dir, ignore_errors=True)


def _cleanup_tmp_courses_dir():
    shutil.rmtree(_tmp_courses_dir, ignore_errors=True)


atexit.register(_cleanup_tmp_db)
atexit.register(_cleanup_tmp_data_dir)
atexit.register(_cleanup_tmp_courses_dir)

os.environ["PROJECTMGR_DB_PATH"] = _tmp_db_path
os.environ["PROJECTMGR_DATA_DIR"] = _tmp_data_dir
os.environ["PROJECTMGR_COURSES_DIR"] = _tmp_courses_dir
os.environ["PROJECTMGR_MAX_BACKUP_BYTES"] = str(2 * 1024 * 1024)  # 2 MB, small enough to test the limit cheaply
os.environ["PROJECTMGR_MAX_WHITEBOARD_ELEMENTS"] = "5"  # small enough to test the limit cheaply
os.environ["ADMIN_USERNAME"] = "testadmin"
os.environ["ADMIN_PASSWORD"] = "S3cur3-Test-Pass!"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-prod"
os.environ["SESSION_HTTPS_ONLY"] = "false"

# fixture course content, isolated from the real /cours directory so tests
# don't break when actual course material changes
_COURSE_FIXTURE_MD = """Cours de test

# Chapitre un
## Sous-titre vide
### Details
Contenu du chapitre un, sous-titre.

# Chapitre deux
Intro du chapitre deux.

![Image](img/demo.png "Demo")
"""

with open(os.path.join(_tmp_courses_dir, "001_test.md"), "w", encoding="utf-8") as _f:
    _f.write(_COURSE_FIXTURE_MD)
with open(os.path.join(_tmp_courses_dir, "002_autre.md"), "w", encoding="utf-8") as _f:
    _f.write("Autre cours\n\n# Un seul chapitre\nTexte.\n")
_QCM_FIXTURE_MD = """--
-- QCM pour Chapitre un
--
# Chapitre un

## Q1
Est-ce une question de test ?
+ oui
- non


## Q2
Sélectionnez les bonnes réponses.
+ a
+ b
- c
"""

with open(os.path.join(_tmp_courses_dir, "001_test_qcm.md"), "w", encoding="utf-8") as _f:
    _f.write(_QCM_FIXTURE_MD)
with open(os.path.join(_tmp_courses_dir, "thématiques.md"), "w", encoding="utf-8") as _f:
    _f.write("- Chapitre un\n- Chapitre deux\n")

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import _login_attempts
from app.routers.sessions import _signup_attempts

PNG_1X1_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_login_rate_limit():
    _login_attempts.clear()
    _signup_attempts.clear()
    yield
    _login_attempts.clear()
    _signup_attempts.clear()


@pytest.fixture()
def gantt_id(client):
    resp = client.post("/gantt/new", follow_redirects=False)
    location = resp.headers["location"]
    return location.rsplit("/", 1)[-1]


@pytest.fixture()
def whiteboard_id(client):
    resp = client.post("/whiteboard/new", follow_redirects=False)
    location = resp.headers["location"]
    return location.rsplit("/", 1)[-1]


@pytest.fixture()
def kanban_id(client):
    resp = client.post("/kanban/new", follow_redirects=False)
    location = resp.headers["location"]
    return location.rsplit("/", 1)[-1]


@pytest.fixture()
def png_bytes():
    return PNG_1X1_BYTES


@pytest.fixture()
def admin_client():
    c = TestClient(app)
    resp = c.post("/login", data={"username": "testadmin", "password": "S3cur3-Test-Pass!"}, follow_redirects=False)
    assert resp.status_code == 303
    return c


@pytest.fixture()
def teacher_client(admin_client):
    # the test db persists for the whole session (not reset per-test), so
    # the username must be unique across every test that uses this fixture
    username = f"teacher_{uuid.uuid4().hex[:12]}"
    resp = admin_client.post(
        "/accounts/teachers", data={"username": username, "password": "TeacherPass123"}, follow_redirects=False
    )
    assert resp.status_code == 303
    c = TestClient(app)
    resp = c.post("/login", data={"username": username, "password": "TeacherPass123"}, follow_redirects=False)
    assert resp.status_code == 303
    return c


@pytest.fixture()
def student_client():
    # there's no admin/teacher-driven "create student" endpoint by design —
    # students self-register via /join/{session_id}/create-account — so
    # tests that just need *a* logged-in student create the row directly
    from app.db import SessionLocal
    from app.models import User
    from app.security import hash_password

    username = f"student_{uuid.uuid4().hex[:12]}"
    password = "StudentPass123"
    db = SessionLocal()
    try:
        db.add(User(username=username, password_hash=hash_password(password), role="student"))
        db.commit()
    finally:
        db.close()

    c = TestClient(app)
    resp = c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert resp.status_code == 303
    return c
