import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATA_DIR = os.environ.get("PROJECTMGR_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.environ.get("PROJECTMGR_DB_PATH") or os.path.join(DATA_DIR, "app.db")

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autoflush=False, autocommit=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# columns added to existing tables after their initial release —
# Base.metadata.create_all() only creates missing *tables*, so a database
# file created before one of these was added needs it patched in by hand.
_NEW_COLUMNS = [
    ("whiteboard_elements", "locked", "BOOLEAN DEFAULT 0"),
    ("activity_log", "payload", "JSON"),
]


def run_migrations(bind_engine=None):
    bind_engine = bind_engine or engine
    inspector = inspect(bind_engine)
    with bind_engine.begin() as conn:
        for table, column, ddl_type in _NEW_COLUMNS:
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
