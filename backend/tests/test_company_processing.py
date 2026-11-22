from collections.abc import Generator
from types import SimpleNamespace
import threading
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import companies as company_routes
from app.db.base import Base
from app.db.session import get_db_session
from app.core.config import Settings
from app.main import app
from app.models import CompanyDocumentVersion
from app.models.document_processing import DocumentExtractionUnit, DocumentProcessingRun, DocumentProcessingRunItem
from app.services.processing_handler import ExtractionUnitData
from app.services.processing_worker import ProcessingWorker, claim_next_item
from app.services.document_storage import DocumentStorage
from app.services.document_validation import (
    validate_company_document_source,
    validate_company_document_staged_source,
)

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\nstartxref\n0\n%%EOF\n"


@pytest.fixture
def company_context(tmp_path, monkeypatch) -> Generator[tuple[TestClient, object, DocumentStorage], None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_fk(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    storage = DocumentStorage(tmp_path / "company-processing-storage")

    def override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[company_routes.get_company_storage] = lambda: storage
    monkeypatch.setattr(
        company_routes,
        "get_settings",
        lambda: SimpleNamespace(tenderwise_max_file_size_bytes=1024, tenderwise_max_batch_size_bytes=2048),
    )
    try:
        with TestClient(app) as client:
            yield client, engine, storage
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(company_routes.get_company_storage, None)
        Base.metadata.drop_all(engine)
        engine.dispose()


def _create_company(client: TestClient, name: str) -> dict:
    response = client.post("/api/companies", json={"legal_name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _upload_version(client: TestClient, company_id: str, title: str = "GST certificate") -> dict:
    response = client.post(
        f"/api/companies/{company_id}/documents",
        files={"file": ("gst.pdf", PDF, "application/pdf")},
        data={"title": title, "category": "gst_certificate", "issue_date": "2026-01-01"},
    )
    assert response.status_code == 201, response.text
    return response.json()["current_version"]


def test_manual_company_processing_run_is_queued_and_scoped(company_context) -> None:
    client, _engine, _storage = company_context
    company = _create_company(client, "Acme Engineering Private Limited")
    version = _upload_version(client, company["id"])

    response = client.post(
        f"/api/companies/{company['id']}/processing-runs",
        json={"version_ids": [version["id"]]},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["company_id"] == company["id"]
    assert body["status"] == "pending"
    assert body["requested_document_count"] == 1
    assert body["items"][0]["company_document_version_id"] == version["id"]
    assert body["items"][0]["status"] == "pending"
    assert body["items"][0]["extraction_unit_count"] == 0

    listed = client.get(f"/api/companies/{company['id']}/processing-runs")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    detail = client.get(f"/api/companies/{company['id']}/processing-runs/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["item_counts"]["pending"] == 1


def test_company_processing_rejects_cross_company_version(company_context) -> None:
    client, _engine, _storage = company_context
    first = _create_company(client, "First Company")
    second = _create_company(client, "Second Company")
    version = _upload_version(client, first["id"])

    response = client.post(
        f"/api/companies/{second['id']}/processing-runs",
        json={"version_ids": [version["id"]]},
    )
    assert response.status_code == 404


def test_archived_company_cannot_start_processing(company_context) -> None:
    client, _engine, _storage = company_context
    company = _create_company(client, "Archived Company")
    version = _upload_version(client, company["id"])
    archived = client.patch(f"/api/companies/{company['id']}", json={"is_archived": True})
    assert archived.status_code == 200

    response = client.post(
        f"/api/companies/{company['id']}/processing-runs",
        json={"version_ids": [version["id"]]},
    )
    assert response.status_code == 409


def test_completed_company_extraction_units_are_retrievable(company_context) -> None:
    client, engine, _storage = company_context
    company = _create_company(client, "Evidence Company")
    uploaded = client.post(
        f"/api/companies/{company['id']}/documents",
        files={"file": ("gst.pdf", PDF, "application/pdf")},
        data={"title": "GST certificate", "category": "gst_certificate"},
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["id"]
    version_id = uploaded.json()["current_version"]["id"]
    queued = client.post(
        f"/api/companies/{company['id']}/processing-runs",
        json={"version_ids": [version_id]},
    )
    assert queued.status_code == 202, queued.text
    run_id = queued.json()["id"]
    item_id = queued.json()["items"][0]["id"]

    with Session(engine) as session:
        item = session.get(DocumentProcessingRunItem, UUID(item_id))
        run = session.get(DocumentProcessingRun, UUID(run_id))
        item.status = "completed"
        item.outcome_code = "processed"
        run.status = "completed"
        session.add(
            DocumentExtractionUnit(
                run_item_id=item.id,
                unit_index=0,
                unit_type="text",
                source_ref={
                    "document_id": document_id,
                    "company_document_id": document_id,
                    "company_document_version_id": version_id,
                    "source_scope": "company",
                    "page": 1,
                    "block_index": 0,
                },
                content_text="GSTIN: 37ABCDE1234F1Z5",
                metadata_json={"format": "pdf", "extractor": "test"},
            )
        )
        session.commit()

    response = client.get(
        f"/api/companies/{company['id']}/documents/{document_id}/versions/{version_id}/extraction-units"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == run_id
    assert body["total"] == 1
    assert body["items"][0]["content_text"] == "GSTIN: 37ABCDE1234F1Z5"
    assert body["items"][0]["source_ref"]["company_document_version_id"] == version_id


def test_company_extraction_units_are_not_visible_through_another_company(company_context) -> None:
    client, _engine, _storage = company_context
    first = _create_company(client, "Private Company One")
    second = _create_company(client, "Private Company Two")
    uploaded = client.post(
        f"/api/companies/{first['id']}/documents",
        files={"file": ("gst.pdf", PDF, "application/pdf")},
        data={"title": "GST certificate", "category": "gst_certificate"},
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["id"]
    version_id = uploaded.json()["current_version"]["id"]

    response = client.get(
        f"/api/companies/{second['id']}/documents/{document_id}/versions/{version_id}/extraction-units"
    )
    assert response.status_code == 404


def test_company_version_validation_checks_private_source_and_staged_copy(company_context):
    client, engine, storage = company_context
    company = _create_company(client, "Validation Company")
    uploaded = client.post(
        f"/api/companies/{company['id']}/documents",
        files={"file": ("gst.pdf", PDF, "application/pdf")},
        data={"title": "GST certificate", "category": "gst_certificate"},
    )
    assert uploaded.status_code == 201, uploaded.text
    version_id = UUID(uploaded.json()["current_version"]["id"])

    with Session(engine) as session:
        version = session.get(CompanyDocumentVersion, version_id)
        assert version is not None
        parent = version.document
        result = validate_company_document_source(
            version, parent, UUID(company["id"]), storage, 1024, "cp1252"
        )
        assert result.valid
        staged = storage.stage_source(version.storage_key)
        try:
            staged_result = validate_company_document_staged_source(
                staged, version, parent, UUID(company["id"]), storage, 1024, "cp1252"
            )
            assert staged_result.valid
        finally:
            storage.cleanup_source_stage(staged)


def test_company_version_claim_and_persist_use_shared_worker_path(company_context, monkeypatch):
    client, engine, storage = company_context
    company = _create_company(client, "Worker Queue Company")
    uploaded = client.post(
        f"/api/companies/{company['id']}/documents",
        files={"file": ("gst.pdf", PDF, "application/pdf")},
        data={"title": "GST certificate", "category": "gst_certificate"},
    )
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["id"]
    version_id = uploaded.json()["current_version"]["id"]
    queued = client.post(
        f"/api/companies/{company['id']}/processing-runs",
        json={"version_ids": [version_id]},
    )
    assert queued.status_code == 202, queued.text
    item_id = UUID(queued.json()["items"][0]["id"])

    # The production worker deliberately requires PostgreSQL for SKIP LOCKED
    # and advisory-lock semantics. This focused SQLite test bypasses only that
    # dialect guard to exercise the company source-selection/persistence path.
    from app.services import processing_worker as worker_module

    monkeypatch.setattr(worker_module, "_require_postgresql", lambda _session: None)
    session_factory = lambda: Session(engine, autoflush=False, autocommit=False)
    settings = Settings(_env_file=None)
    claim = claim_next_item(session_factory, "sqlite-company-worker", settings, storage)
    assert claim is not None and claim.item_id == item_id
    assert claim.document_id is None
    assert claim.company_document_id == UUID(document_id)
    assert claim.company_document_version_id == UUID(version_id)

    class StubCompanyHandler:
        def process(self, context):
            return [
                ExtractionUnitData(
                    unit_type="text",
                    source_ref={"document_id": str(context.document_id), "page": 1, "block_index": 0},
                    content_text="Company registration evidence",
                    metadata={"format": "pdf", "extractor": "test"},
                )
            ]

    worker = ProcessingWorker(
        session_factory, settings, storage, StubCompanyHandler(), worker_id=claim.worker_id
    )
    worker._process_claim(claim, threading.Event())

    with Session(engine) as session:
        item = session.get(DocumentProcessingRunItem, item_id)
        units = list(
            session.query(DocumentExtractionUnit)
            .filter(DocumentExtractionUnit.run_item_id == item_id)
            .all()
        )
        assert item is not None and item.status == "completed"
        assert len(units) == 1
        assert units[0].source_ref["source_scope"] == "company"
        assert units[0].source_ref["company_document_id"] == document_id
        assert units[0].source_ref["company_document_version_id"] == version_id
        assert units[0].content_text == "Company registration evidence"
