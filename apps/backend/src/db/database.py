"""SQLAlchemy engine, session factory, and FastAPI dependency."""

from __future__ import annotations

import logging
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import event, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

_connect_args: dict = {}
_pool_class = None

# SQLite-specific tweaks
if settings.DATABASE_URL.startswith("sqlite"):
    _connect_args["check_same_thread"] = False
    _pool_class = StaticPool

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_connect_args,
    poolclass=_pool_class,
    echo=settings.DEBUG,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record):
    """Enable WAL mode and set busy timeout for SQLite connections."""
    # Only apply to SQLite (pysqlite) connections
    module_name = type(dbapi_connection).__module__
    if "sqlite" in module_name:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA busy_timeout=5000;")
            cursor.execute("PRAGMA foreign_keys=ON;")
        finally:
            cursor.close()


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_db() -> None:
    """Create all tables defined on the declarative Base.

    Safe to call multiple times — SQLAlchemy's ``create_all`` is a no-op
    for tables that already exist.
    """
    from src.db.models import Base  # noqa: F811 — imported here to avoid circular deps

    # Ensure the data/ directory exists for SQLite
    if settings.DATABASE_URL.startswith("sqlite"):
        # Extract the file path after "sqlite:///" safely
        db_path = settings.DATABASE_URL.split("///", 1)[-1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created / verified.")


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a scoped session and auto-closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
