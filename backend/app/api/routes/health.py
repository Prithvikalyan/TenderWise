from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import create_database_engine

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def application_health() -> dict[str, str]:
    """Report that the API process is responding."""
    return {"status": "ok", "service": "TenderWise API"}


@router.get("/database")
def database_health() -> dict[str, str]:
    """Verify PostgreSQL by opening a connection and executing SELECT 1."""
    database_url = get_settings().database_url
    if not database_url:
        raise HTTPException(status_code=503, detail="Database is not configured")

    engine = None
    try:
        engine = create_database_engine(database_url)
        with engine.connect() as connection:
            if connection.execute(text("SELECT 1")).scalar_one() != 1:
                raise SQLAlchemyError("Database health query returned an unexpected result")
    except (SQLAlchemyError, ValueError):
        raise HTTPException(status_code=503, detail="Database is unavailable") from None
    finally:
        if engine is not None:
            engine.dispose()

    return {"status": "ok", "service": "PostgreSQL"}
