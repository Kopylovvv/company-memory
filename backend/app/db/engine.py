"""Shared SQLAlchemy engine and session factory.

The engine connects lazily: importing this module, or the app, never requires
a reachable database. Only executing a query does. This keeps `/api/health`
independent of database readiness (see docs/architecture.md).
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.settings import load_database_settings

engine = create_engine(
    load_database_settings().url,
    pool_pre_ping=True,
    connect_args={"options": "-c timezone=utc", "connect_timeout": 5},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
