from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def create_database_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine for the configured database URL."""
    return create_engine(database_url, pool_pre_ping=True)


@lru_cache
def get_engine() -> Engine:
    """Return the configured application engine, creating it lazily."""
    database_url = get_settings().database_url
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return create_database_engine(database_url)


def get_db_session() -> Generator[Session, None, None]:
    """Yield a database session and always close it after the request."""
    session_factory = sessionmaker(bind=get_engine(), class_=Session, autoflush=False, autocommit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
