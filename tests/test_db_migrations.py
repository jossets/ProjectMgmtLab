"""Regression test for run_migrations(): production databases created
before `locked`/`payload` were added to the models must get those columns
patched in, instead of the app crashing with
"sqlite3.OperationalError: no such column" (see bug report from
projects.yolospacehacker.com after deploying the undo-delete feature).
"""
import os
import tempfile

from sqlalchemy import create_engine, inspect, text

from app.db import run_migrations


def _make_pre_migration_db(path):
    """Build a whiteboard_elements/activity_log schema as it existed
    before `locked` and `payload` were added to the models."""
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE whiteboards (
                id VARCHAR(32) PRIMARY KEY
            )
        """))
        conn.execute(text("""
            CREATE TABLE whiteboard_elements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                whiteboard_id VARCHAR(32),
                type VARCHAR(20),
                x FLOAT,
                y FLOAT,
                width FLOAT,
                height FLOAT,
                z_index INTEGER,
                data JSON,
                updated_at DATETIME
            )
        """))
        conn.execute(text("""
            CREATE TABLE activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_type VARCHAR(20),
                tool_id VARCHAR(32),
                actor_id VARCHAR(32),
                actor_label VARCHAR(50),
                action VARCHAR(20),
                summary VARCHAR(300),
                created_at DATETIME
            )
        """))
        conn.execute(text(
            "INSERT INTO whiteboards (id) VALUES ('wb1')"
        ))
        conn.execute(text(
            "INSERT INTO whiteboard_elements "
            "(whiteboard_id, type, x, y, width, height, z_index, data) "
            "VALUES ('wb1', 'text', 0, 0, 100, 100, 0, '{}')"
        ))
    return engine


def test_run_migrations_adds_missing_columns_to_an_old_database():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        engine = _make_pre_migration_db(path)

        inspector = inspect(engine)
        before = {c["name"] for c in inspector.get_columns("whiteboard_elements")}
        assert "locked" not in before
        before_log = {c["name"] for c in inspector.get_columns("activity_log")}
        assert "payload" not in before_log

        run_migrations(engine)

        inspector = inspect(engine)
        after = {c["name"] for c in inspector.get_columns("whiteboard_elements")}
        assert "locked" in after
        after_log = {c["name"] for c in inspector.get_columns("activity_log")}
        assert "payload" in after_log

        # the pre-existing row must still be readable, with the new column
        # defaulting sanely instead of erroring out
        with engine.begin() as conn:
            row = conn.execute(text(
                "SELECT locked FROM whiteboard_elements WHERE whiteboard_id = 'wb1'"
            )).fetchone()
        assert row[0] in (0, None)
    finally:
        engine.dispose()
        os.remove(path)


def test_run_migrations_is_idempotent():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        engine = _make_pre_migration_db(path)
        run_migrations(engine)
        # a second call must not raise (e.g. "duplicate column name")
        run_migrations(engine)

        inspector = inspect(engine)
        columns = [c["name"] for c in inspector.get_columns("whiteboard_elements")]
        assert columns.count("locked") == 1
    finally:
        engine.dispose()
        os.remove(path)


def test_run_migrations_on_a_fresh_database_is_a_noop():
    # the app's own test database is already created via Base.metadata.create_all()
    # with every current column present — running the migration again must
    # not error and must leave the schema untouched.
    from app.db import engine

    run_migrations(engine)

    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("whiteboard_elements")}
    assert "locked" in columns
