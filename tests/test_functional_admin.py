import uuid


def _uname(prefix="admintest"):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def test_backup_download_is_a_sqlite_file(admin_client):
    resp = admin_client.get("/admin/backup")
    assert resp.status_code == 200
    assert resp.content.startswith(b"SQLite format 3\x00")
    assert "attachment" in resp.headers.get("content-disposition", "")
    assert ".db" in resp.headers.get("content-disposition", "")


def test_restore_round_trip_reverts_only_post_backup_changes(admin_client):
    # a teacher created *before* the backup must survive the restore
    kept_username = _uname("kept")
    admin_client.post("/accounts/teachers", data={"username": kept_username, "password": "TeacherPass123"})

    backup = admin_client.get("/admin/backup")
    assert backup.status_code == 200

    # created *after* the backup — must be gone once we restore it
    dropped_username = _uname("dropped")
    admin_client.post("/accounts/teachers", data={"username": dropped_username, "password": "TeacherPass123"})
    accounts_before_restore = admin_client.get("/accounts").text
    assert dropped_username in accounts_before_restore

    restore = admin_client.post(
        "/admin/restore",
        files={"file": ("backup.db", backup.content, "application/octet-stream")},
        follow_redirects=False,
    )
    assert restore.status_code == 303

    accounts_after_restore = admin_client.get("/accounts").text
    assert kept_username in accounts_after_restore
    assert dropped_username not in accounts_after_restore
