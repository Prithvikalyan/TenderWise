from collections.abc import Generator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db_session
from app.main import app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_fk(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)

    def override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_qa_conversation_persists_messages_and_retrieval_telemetry(client: TestClient) -> None:
    created = client.post("/api/tenders", json={"name": "Q&A persistence tender"})
    assert created.status_code == 201, created.text
    tender_id = created.json()["id"]

    answer = client.post(
        f"/api/tenders/{tender_id}/qa",
        json={"question": "What is the submission deadline?"},
    )
    assert answer.status_code == 200, answer.text
    body = answer.json()
    conversation_id = body["conversation_id"]
    assert conversation_id
    assert body["evidence_found"] is False
    assert body["citation_validation_status"] == "no_sources"

    listing = client.get(f"/api/tenders/{tender_id}/qa/conversations")
    assert listing.status_code == 200
    assert listing.json()[0]["id"] == conversation_id
    assert listing.json()[0]["message_count"] == 2

    detail = client.get(f"/api/tenders/{tender_id}/qa/conversations/{conversation_id}")
    assert detail.status_code == 200
    messages = detail.json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "What is the submission deadline?"
    assert messages[1]["response_json"]["conversation_id"] == conversation_id

    metrics = client.get(f"/api/tenders/{tender_id}/qa/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["total_questions"] == 1
    assert metrics.json()["no_evidence_count"] == 1

    removed = client.delete(f"/api/tenders/{tender_id}/qa/conversations/{conversation_id}")
    assert removed.status_code == 204
    assert client.get(f"/api/tenders/{tender_id}/qa/conversations").json() == []


def test_retrieval_status_reports_sqlite_as_not_pgvector_ready(client: TestClient) -> None:
    response = client.get("/api/qa/retrieval-status")
    assert response.status_code == 200
    status = response.json()
    assert status["database_dialect"] == "sqlite"
    assert status["pgvector_available"] is False
    assert status["dense_retrieval_ready"] is False
    assert status["embedding_dimensions"] == 2048
    assert status["hnsw_ef_search"] == 100
    assert status["candidate_k"] == 50
    assert status["rrf_k"] == 60
    assert status["chunker_version"]


def test_conversation_is_scoped_to_tender(client: TestClient) -> None:
    first = client.post("/api/tenders", json={"name": "First"}).json()
    second = client.post("/api/tenders", json={"name": "Second"}).json()
    created = client.post(f"/api/tenders/{first['id']}/qa/conversations")
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    response = client.get(f"/api/tenders/{second['id']}/qa/conversations/{conversation_id}")
    assert response.status_code == 404
