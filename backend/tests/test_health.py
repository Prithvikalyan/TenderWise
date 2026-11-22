from fastapi.testclient import TestClient

from app.api.routes import health
from app.main import app

client = TestClient(app)


def test_application_health() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "TenderWise API"}


def test_database_health_unavailable_when_unconfigured(monkeypatch) -> None:
    monkeypatch.setattr(health, "get_settings", lambda: type("Settings", (), {"database_url": None})())

    response = client.get("/api/health/database")

    assert response.status_code == 503
    assert response.json()["detail"] == "Database is not configured"


def test_database_health_requires_successful_select_one(monkeypatch) -> None:
    monkeypatch.setattr(health, "get_settings", lambda: type("Settings", (), {"database_url": "postgresql+psycopg://test"})())

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement):
            assert str(statement) == "SELECT 1"
            class Result:
                @staticmethod
                def scalar_one():
                    return 1

            return Result()

    class Engine:
        def connect(self):
            return Connection()

        def dispose(self):
            pass

    monkeypatch.setattr(health, "create_database_engine", lambda _url: Engine())

    response = client.get("/api/health/database")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "PostgreSQL"}


def test_database_health_reports_unavailable_when_query_fails(monkeypatch) -> None:
    from sqlalchemy.exc import OperationalError

    monkeypatch.setattr(health, "get_settings", lambda: type("Settings", (), {"database_url": "postgresql+psycopg://test"})())

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _statement):
            raise OperationalError("SELECT 1", {}, RuntimeError("connection refused"))

    class Engine:
        def connect(self):
            return Connection()

        def dispose(self):
            pass

    monkeypatch.setattr(health, "create_database_engine", lambda _url: Engine())

    response = client.get("/api/health/database")

    assert response.status_code == 503
    assert response.json()["detail"] == "Database is unavailable"
