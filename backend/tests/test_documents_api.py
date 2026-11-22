from collections.abc import Generator
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import documents as document_routes
from app.db.base import Base
from app.db.session import get_db_session
from app.main import app
from app.models import Tender, TenderDocument
from app.services.document_storage import DocumentStorage

PDF = b"%PDF-1.7\nTenderWise test file\n%%EOF"


@pytest.fixture
def api_context(tmp_path, monkeypatch) -> Generator[tuple[TestClient, object, DocumentStorage], None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def enable_fk(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    storage = DocumentStorage(tmp_path / "private-store")

    def override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[document_routes.get_storage] = lambda: storage
    monkeypatch.setattr(document_routes, "get_settings", lambda: SimpleNamespace(tenderwise_max_file_size_bytes=50, tenderwise_max_batch_size_bytes=80))
    try:
        with TestClient(app) as client:
            yield client, engine, storage
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(document_routes.get_storage, None)
        Base.metadata.drop_all(engine)
        engine.dispose()


def tender(client: TestClient) -> dict:
    response = client.post("/api/tenders", json={"name": "Upload workspace"})
    assert response.status_code == 201, f"{response.text}; headers={response.request.headers}"
    return response.json()


def test_upload_openapi_exposes_file_selection_and_repeated_categories(api_context) -> None:
    client, _engine, _storage = api_context
    spec = client.get("/openapi.json").json()
    request_schema_ref = spec["paths"]["/api/tenders/{tender_id}/documents"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
    request_schema = spec["components"]["schemas"][request_schema_ref.rsplit("/", 1)[-1]]
    properties = request_schema["properties"]
    assert properties["files"] == {"items": {"type": "string", "format": "binary"}, "type": "array", "title": "Files"}
    assert properties["categories"] == {"items": {"type": "string"}, "type": "array", "title": "Categories"}


def upload(client: TestClient, tender_id: str, entries: list[tuple[str, bytes, str]], category_values: list[str] | None = None) -> object:
    multipart_fields = [("files", (name, content, content_type)) for name, content, content_type in entries]
    multipart_fields.extend(
        ("categories", (None, category))
        for category in (category_values or ["main_tender"] * len(entries))
    )
    return client.post(f"/api/tenders/{tender_id}/documents", files=multipart_fields)


def test_upload_single_multiple_duplicate_names_and_hidden_storage_key(api_context) -> None:
    client, _engine, storage = api_context
    parent = tender(client)
    response = upload(client, parent["id"], [("same.pdf", PDF, "text/plain"), ("same.pdf", PDF + b"2", "image/png")], ["main_tender", "boq"])
    assert response.status_code == 201, f"{response.text}; headers={response.request.headers}"
    items = response.json()
    assert len(items) == 2
    assert items[0]["original_filename"] == items[1]["original_filename"] == "same.pdf"
    assert "storage_key" not in str(response.json())
    assert len(list(storage.root.glob("*.pdf"))) == 2
    assert [item["category"] for item in items] == ["main_tender", "boq"]


@pytest.mark.parametrize("filename,data,expected", [("empty.pdf", b"", 422), ("bad.exe", PDF, 415), ("fake.pdf", b"not a pdf", 415), ("../escape.pdf", PDF, 422)])
def test_upload_rejects_invalid_files(api_context, filename: str, data: bytes, expected: int) -> None:
    client, _engine, storage = api_context
    parent = tender(client)
    response = upload(client, parent["id"], [(filename, data, "application/pdf")])
    assert response.status_code == expected, response.text
    assert list(storage.root.glob("*.pdf")) == []


def test_upload_mismatch_invalid_category_size_limits_and_atomic_cleanup(api_context) -> None:
    client, engine, storage = api_context
    parent = tender(client)
    base = f"/api/tenders/{parent['id']}/documents"
    assert client.post(base, files=[("files", ("a.pdf", PDF, "application/pdf"))]).status_code == 422
    invalid_category = client.post(base, files=[("files", ("a.pdf", PDF, "application/pdf")), ("categories", (None, "made_up"))])
    assert invalid_category.status_code == 422
    assert invalid_category.json() == {"detail": "Invalid document category"}
    assert upload(client, parent["id"], [("large.pdf", PDF * 2, "application/pdf")]).status_code == 413
    assert upload(client, parent["id"], [("a.pdf", PDF, "application/pdf"), ("b.pdf", PDF, "application/pdf"), ("c.pdf", PDF, "application/pdf")]).status_code == 413
    assert list(storage.root.glob("*.pdf")) == []
    with Session(engine) as session:
        assert session.scalar(select(TenderDocument)) is None


def test_list_filter_update_download_and_delete(api_context) -> None:
    client, engine, storage = api_context
    parent = tender(client)
    upload_response = upload(client, parent["id"], [("integrity.pdf", PDF, "text/plain")])
    assert upload_response.status_code == 201, upload_response.text
    created = upload_response.json()[0]
    doc_id = created["id"]
    path = storage.resolve_key(next(iter(storage.root.glob("*.pdf"))).name)
    assert client.get(f"/api/tenders/{parent['id']}/documents", params={"category": "main_tender"}).json()["total"] == 1
    assert client.get(f"/api/tenders/{parent['id']}/documents", params={"category": "boq"}).json()["total"] == 0
    changed = client.patch(f"/api/tenders/{parent['id']}/documents/{doc_id}", json={"category": "boq"})
    assert changed.status_code == 200 and changed.json()["category"] == "boq"
    downloaded = client.get(f"/api/tenders/{parent['id']}/documents/{doc_id}/download")
    assert downloaded.content == PDF
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"/api/tenders/00000000-0000-0000-0000-000000000001/documents/{doc_id}/download").status_code == 404
    assert client.delete(f"/api/tenders/{parent['id']}/documents/{doc_id}").status_code == 204
    assert not path.exists()
    with Session(engine) as session:
        assert session.get(TenderDocument, UUID(doc_id)) is None


def test_delete_returns_404_when_document_disappears_before_locked_delete(api_context, monkeypatch) -> None:
    client, _engine, _storage = api_context
    parent = tender(client)
    response = upload(client, parent["id"], [("raced.pdf", PDF, "application/pdf")])
    document_id = response.json()[0]["id"]

    def disappeared(*_args):
        raise LookupError("internal row-lock race detail")

    monkeypatch.setattr(document_routes.document_service, "delete_document", disappeared)
    result = client.delete(f"/api/tenders/{parent['id']}/documents/{document_id}")
    assert result.status_code == 404
    assert result.json() == {"detail": "Document not found"}
    assert "internal" not in result.text


def test_upload_returns_safe_404_if_tender_disappears_before_lock(api_context, monkeypatch) -> None:
    client, _engine, storage = api_context
    parent = tender(client)

    def disappeared(*_args, **_kwargs):
        raise document_routes.document_service.TenderNotFoundError("private race detail")

    monkeypatch.setattr(document_routes.document_service, "upload_documents", disappeared)
    response = upload(client, parent["id"], [("raced.pdf", PDF, "application/pdf")])
    assert response.status_code == 404
    assert response.json() == {"detail": "Tender not found"}
    assert "private" not in response.text
    assert list(storage.root.glob("*.pdf")) == []


def test_missing_physical_file_returns_safe_error(api_context) -> None:
    client, _engine, storage = api_context
    parent = tender(client)
    upload_response = upload(client, parent["id"], [("lost.pdf", PDF, "application/pdf")])
    assert upload_response.status_code == 201, upload_response.text
    record = upload_response.json()[0]
    key = next(iter(storage.root.glob("*.pdf"))).name
    storage.remove(key)
    response = client.get(f"/api/tenders/{parent['id']}/documents/{record['id']}/download")
    assert response.status_code == 404
    assert "storage" not in response.text.lower()


def test_database_commit_failure_removes_promoted_file(api_context) -> None:
    client, engine, storage = api_context
    parent = tender(client)

    def fail_commit(_session) -> None:
        raise SQLAlchemyError("simulated isolated database failure")

    event.listen(Session, "before_commit", fail_commit)
    try:
        response = upload(client, parent["id"], [("rollback.pdf", PDF, "application/pdf")])
    finally:
        event.remove(Session, "before_commit", fail_commit)
    assert response.status_code == 500, response.text
    assert list(storage.root.glob("*.pdf")) == []
    assert list(storage.staging.glob("*.part")) == []
    assert list(storage.staging.glob("*.lease")) == []
    with Session(engine) as session:
        assert session.scalar(select(TenderDocument)) is None


def test_tender_delete_removes_associated_physical_file(api_context, monkeypatch) -> None:
    import app.services.tenders as tender_service

    client, _engine, storage = api_context
    parent = tender(client)
    upload(client, parent["id"], [("held.pdf", PDF, "application/pdf")])
    monkeypatch.setattr(tender_service, "DocumentStorage", lambda: storage)
    assert client.delete(f"/api/tenders/{parent['id']}").status_code == 204
    assert list(storage.root.glob("*.pdf")) == []
    assert list(storage.quarantine.glob("*.held")) == []
    assert list(storage.quarantine.glob("*.json")) == []


def test_tender_delete_database_failure_restores_quarantined_file(api_context, monkeypatch) -> None:
    import app.services.tenders as tender_service

    client, engine, storage = api_context
    parent = tender(client)
    upload(client, parent["id"], [("restore.pdf", PDF, "application/pdf")])
    stored_path = next(storage.root.glob("*.pdf"))
    monkeypatch.setattr(tender_service, "DocumentStorage", lambda: storage)

    def fail_commit(_session) -> None:
        raise SQLAlchemyError("simulated isolated database failure")

    monkeypatch.setattr(tender_service, "_commit", fail_commit)
    event.listen(Session, "before_commit", fail_commit)
    try:
        response = client.delete(f"/api/tenders/{parent['id']}")
    finally:
        event.remove(Session, "before_commit", fail_commit)
    assert response.status_code == 503
    assert stored_path.exists()
    assert list(storage.quarantine.glob("*.held")) == []
    assert list(storage.quarantine.glob("*.json")) == []
    with Session(engine) as session:
        assert session.get(Tender, UUID(parent["id"])) is not None
        assert session.scalar(select(TenderDocument)) is not None
