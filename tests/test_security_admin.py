import sqlite3
import tempfile

import pytest


def test_backup_requires_admin_not_just_teacher(client, teacher_client, student_client):
    for c in (client, teacher_client, student_client):
        resp = c.get("/admin/backup", follow_redirects=False)
        assert resp.status_code in (303, 403)


def test_restore_requires_admin_not_just_teacher(client, teacher_client, student_client):
    for c in (client, teacher_client, student_client):
        resp = c.post(
            "/admin/restore",
            files={"file": ("x.db", b"whatever", "application/octet-stream")},
            follow_redirects=False,
        )
        assert resp.status_code in (303, 403)


def test_restore_rejects_non_sqlite_file(admin_client):
    resp = admin_client.post(
        "/admin/restore",
        files={"file": ("evil.db", b"not a real sqlite database", "application/octet-stream")},
    )
    assert resp.status_code == 415


def test_restore_rejects_oversized_file(admin_client):
    # PROJECTMGR_MAX_BACKUP_BYTES is set to 2MB for tests
    oversized = b"SQLite format 3\x00" + b"\x00" * (2 * 1024 * 1024 + 1)
    resp = admin_client.post(
        "/admin/restore",
        files={"file": ("big.db", oversized, "application/octet-stream")},
    )
    assert resp.status_code == 413


def test_restore_rejects_sqlite_file_without_users_table(admin_client):
    fd, path = tempfile.mkstemp(suffix=".db")
    import os

    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        with open(path, "rb") as f:
            content = f.read()
    finally:
        os.remove(path)

    resp = admin_client.post(
        "/admin/restore",
        files={"file": ("notmine.db", content, "application/octet-stream")},
    )
    assert resp.status_code == 422


def test_restore_rejects_corrupt_sqlite_file(admin_client):
    # valid magic header, but garbage after it — fails PRAGMA integrity_check
    corrupt = b"SQLite format 3\x00" + b"\xff" * 4096
    resp = admin_client.post(
        "/admin/restore",
        files={"file": ("corrupt.db", corrupt, "application/octet-stream")},
    )
    assert resp.status_code == 422
