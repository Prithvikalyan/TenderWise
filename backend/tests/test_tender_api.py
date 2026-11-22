from collections.abc import Generator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db_session
from app.main import app
from app.models import DocumentCategory, Tender, TenderDocument
from app.services import tenders as tender_service
from app.services.document_storage import DocumentStorage


@pytest.fixture
def api_context(tmp_path, monkeypatch) -> Generator[tuple[TestClient, Engine], None, None]:
    """Run each API test against a fresh isolated in-memory SQLite database."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    monkeypatch.setattr(tender_service, "DocumentStorage", lambda: DocumentStorage(tmp_path / "private-storage"))

    def override_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    try:
        with TestClient(app) as client:
            yield client, engine
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        Base.metadata.drop_all(engine)
        engine.dispose()


def create_workspace(client: TestClient, name: str, **fields) -> dict:
    response = client.post("/api/tenders", json={"name": name, **fields})
    assert response.status_code == 201
    return response.json()


def test_create_tender_with_required_fields_and_persist_it(api_context) -> None:
    client, engine = api_context

    result = create_workspace(client, "Road maintenance tender")

    assert result["name"] == "Road maintenance tender"
    assert result["status"] == "draft"
    assert UUID(result["id"])
    with Session(engine) as session:
        stored = session.get(Tender, UUID(result["id"]))
        assert stored is not None
        assert stored.name == "Road maintenance tender"


def test_create_tender_with_all_optional_fields(api_context) -> None:
    client, _engine = api_context

    result = create_workspace(
        client,
        "Bridge tender",
        reference_number="REF-2026-42",
        issuing_organization="Public Works Authority",
        department="Bridges Division",
        description="Maintenance and repair.",
    )

    assert result["reference_number"] == "REF-2026-42"
    assert result["issuing_organization"] == "Public Works Authority"
    assert result["department"] == "Bridges Division"
    assert result["description"] == "Maintenance and repair."


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_create_rejects_empty_or_whitespace_name(api_context, name: str) -> None:
    client, _engine = api_context

    response = client.post("/api/tenders", json={"name": name})

    assert response.status_code == 422


def test_list_tenders_returns_paginated_envelope(api_context) -> None:
    client, _engine = api_context
    create_workspace(client, "Tender one")
    create_workspace(client, "Tender two")

    response = client.get("/api/tenders")

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert response.json()["limit"] == 20
    assert response.json()["offset"] == 0
    assert len(response.json()["items"]) == 2


def test_list_searches_name_reference_and_organization(api_context) -> None:
    client, _engine = api_context
    create_workspace(client, "North road", reference_number="REF-101")
    create_workspace(client, "Water project", issuing_organization="North Regional Board")
    create_workspace(client, "South bridge", reference_number="REF-202")

    for term, expected_name in (
        ("road", "North road"),
        ("ref-101", "North road"),
        ("regional board", "Water project"),
    ):
        response = client.get("/api/tenders", params={"search": term})
        assert response.status_code == 200
        assert [item["name"] for item in response.json()["items"]] == [expected_name]
        assert response.json()["total"] == 1


def test_list_filters_status(api_context) -> None:
    client, _engine = api_context
    draft = create_workspace(client, "Draft workspace")
    active = create_workspace(client, "Active workspace")
    response = client.patch(f"/api/tenders/{active['id']}", json={"status": "active"})
    assert response.status_code == 200

    result = client.get("/api/tenders", params={"status": "draft"}).json()

    assert result["total"] == 1
    assert result["items"][0]["id"] == draft["id"]


def test_list_pagination_and_limit_validation(api_context) -> None:
    client, engine = api_context
    for number in range(3):
        create_workspace(client, f"Tender {number}")
    with Session(engine) as session:
        session.execute(update(Tender).values(updated_at=datetime(2026, 1, 1, tzinfo=UTC)))
        session.commit()

    first_page = client.get("/api/tenders", params={"limit": 2, "offset": 0}).json()
    second_page = client.get("/api/tenders", params={"limit": 2, "offset": 2}).json()

    assert first_page["total"] == 3
    assert len(first_page["items"]) == 2
    assert len(second_page["items"]) == 1
    assert first_page["items"][0]["id"] != first_page["items"][1]["id"]
    assert [item["id"] for item in first_page["items"]] == sorted(
        (item["id"] for item in first_page["items"]), reverse=True
    )
    assert client.get("/api/tenders", params={"limit": 101}).status_code == 422


def test_get_tender_includes_document_count(api_context) -> None:
    client, engine = api_context
    tender_data = create_workspace(client, "Workspace with documents")
    with Session(engine) as session:
        tender = session.get(Tender, UUID(tender_data["id"]))
        assert tender is not None
        tender.documents.extend(
            [
                TenderDocument(
                    original_filename=f"file-{number}.pdf",
                    storage_key=f"{tender.id}-{number}",
                    extension=".pdf",
                    content_type="application/pdf",
                    size_bytes=123,
                    category=DocumentCategory.MAIN_TENDER,
                )
                for number in range(2)
            ]
        )
        session.commit()

    response = client.get(f"/api/tenders/{tender_data['id']}")

    assert response.status_code == 200
    assert response.json()["document_count"] == 2
    assert response.json()["name"] == "Workspace with documents"


def test_get_tender_with_zero_documents_returns_zero_count(api_context) -> None:
    client, _engine = api_context
    tender = create_workspace(client, "Empty workspace")

    response = client.get(f"/api/tenders/{tender['id']}")

    assert response.status_code == 200
    assert response.json()["document_count"] == 0


def test_update_tender_fields_and_clear_optional_values(api_context) -> None:
    client, _engine = api_context
    tender = create_workspace(client, "Original", reference_number="R-7", description="Details")

    response = client.patch(
        f"/api/tenders/{tender['id']}",
        json={"name": "Updated", "reference_number": None, "description": None},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Updated"
    assert response.json()["reference_number"] is None
    assert response.json()["description"] is None
    assert response.json()["updated_at"] >= tender["updated_at"]


def test_update_status_and_reject_invalid_status(api_context) -> None:
    client, _engine = api_context
    tender = create_workspace(client, "Status test")

    updated = client.patch(f"/api/tenders/{tender['id']}", json={"status": "archived"})
    invalid = client.patch(f"/api/tenders/{tender['id']}", json={"status": "submitted"})

    assert updated.status_code == 200
    assert updated.json()["status"] == "archived"
    assert invalid.status_code == 422


def test_invalid_uuid_is_validation_error(api_context) -> None:
    client, _engine = api_context

    response = client.get("/api/tenders/not-a-uuid")

    assert response.status_code == 422


@pytest.mark.parametrize("method,payload", [("get", None), ("patch", {"name": "changed"}), ("delete", None)])
def test_missing_tender_returns_404(api_context, method: str, payload: dict | None) -> None:
    client, _engine = api_context
    url = "/api/tenders/00000000-0000-0000-0000-000000000123"

    response = getattr(client, method)(url, json=payload) if payload is not None else getattr(client, method)(url)

    assert response.status_code == 404
    assert response.json()["detail"] == "Tender not found"


def test_delete_tender_returns_204_and_cascades_document_metadata(api_context) -> None:
    client, engine = api_context
    tender_data = create_workspace(client, "Delete workspace")
    with Session(engine) as session:
        tender = session.get(Tender, UUID(tender_data["id"]))
        assert tender is not None
        session.add(
            TenderDocument(
                tender=tender,
                original_filename="source.pdf",
                storage_key="delete-test-storage-key",
                extension=".pdf",
                content_type="application/pdf",
                size_bytes=123,
                category=DocumentCategory.MAIN_TENDER,
            )
        )
        session.commit()

    response = client.delete(f"/api/tenders/{tender_data['id']}")

    assert response.status_code == 204
    with Session(engine) as session:
        assert session.get(Tender, UUID(tender_data["id"])) is None
        assert session.scalar(select(func.count()).select_from(TenderDocument)) == 0
