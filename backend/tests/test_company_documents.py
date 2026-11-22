from collections.abc import Generator
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import importlib.util
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import companies as company_routes
from app.db.base import Base
from app.db.session import get_db_session
from app.main import app
from app.models import Company, CompanyDocument, CompanyDocumentCategory, CompanyDocumentVersion
from app.services.document_storage import DocumentStorage

PDF = b"%PDF-1.7\nTenderWise company file test\n%%EOF"


@pytest.fixture
def api_context(tmp_path, monkeypatch) -> Generator[tuple[TestClient, object, DocumentStorage], None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_fk(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    storage = DocumentStorage(tmp_path / "private-company-store")

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


def create_company(client: TestClient, name: str = "Acme Infrastructure Private Limited") -> dict:
    response = client.post(
        "/api/companies",
        json={"legal_name": name, "display_name": "Acme Infrastructure", "registration_number": "CIN-TEST-001"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload_document(
    client: TestClient,
    company_id: str,
    *,
    filename: str = "gst-certificate.pdf",
    content: bytes = PDF,
    title: str = "GST Registration Certificate",
    category: str = "gst_certificate",
    extra: dict | None = None,
):
    data = {"title": title, "category": category, **(extra or {})}
    return client.post(
        f"/api/companies/{company_id}/documents",
        files={"file": (filename, content, "application/pdf")},
        data=data,
    )


def upload_version(client: TestClient, company_id: str, document_id: str, *, content: bytes = PDF + b"-v2", extra=None):
    return client.post(
        f"/api/companies/{company_id}/documents/{document_id}/versions",
        files={"file": ("gst-certificate-updated.pdf", content, "application/pdf")},
        data=extra or {},
    )


def test_create_list_search_and_archive_company(api_context) -> None:
    client, _engine, _storage = api_context
    company = create_company(client)
    assert company["legal_name"] == "Acme Infrastructure Private Limited"
    assert company["is_archived"] is False

    found = client.get("/api/companies", params={"search": "Acme"}).json()
    assert found["total"] == 1
    assert found["items"][0]["id"] == company["id"]

    archived = client.patch(f"/api/companies/{company['id']}", json={"is_archived": True})
    assert archived.status_code == 200
    assert client.get("/api/companies").json()["total"] == 0
    assert client.get("/api/companies", params={"include_archived": "true"}).json()["total"] == 1


def test_create_company_rejects_blank_legal_name(api_context) -> None:
    client, _engine, _storage = api_context
    response = client.post("/api/companies", json={"legal_name": "  "})
    assert response.status_code == 422


def test_upload_company_document_creates_version_one_and_keeps_storage_private(api_context) -> None:
    client, _engine, storage = api_context
    company = create_company(client)
    response = upload_document(
        client,
        company["id"],
        extra={
            "issue_date": "2026-04-01",
            "expiry_date": "2027-03-31",
            "financial_year": "2026-27",
            "issuer": "Government Authority",
            "reference_number": "GST-REF-009",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == "GST Registration Certificate"
    assert body["category"] == "gst_certificate"
    assert body["version_count"] == 1
    assert body["current_version"]["version_number"] == 1
    assert body["current_version"]["issue_date"] == "2026-04-01"
    assert body["current_version"]["expiry_date"] == "2027-03-31"
    assert body["current_version"]["financial_year"] == "2026-27"
    assert "storage_key" not in response.text
    assert len(list(storage.root.glob("*.pdf"))) == 1


def test_versions_are_immutable_revisions_and_downloadable(api_context) -> None:
    client, _engine, storage = api_context
    company = create_company(client)
    first = upload_document(client, company["id"])
    assert first.status_code == 201, first.text
    document_id = first.json()["id"]

    second = upload_version(
        client,
        company["id"],
        document_id,
        extra={"issue_date": "2026-05-01", "financial_year": "2026-27"},
    )
    assert second.status_code == 201, second.text
    assert second.json()["version_number"] == 2

    listing = client.get(f"/api/companies/{company['id']}/documents").json()
    document = listing["items"][0]
    assert document["version_count"] == 2
    assert document["current_version"]["version_number"] == 2

    history = client.get(
        f"/api/companies/{company['id']}/documents/{document_id}/versions"
    )
    assert history.status_code == 200
    assert [item["version_number"] for item in history.json()["items"]] == [2, 1]

    latest_id = second.json()["id"]
    downloaded = client.get(
        f"/api/companies/{company['id']}/documents/{document_id}/versions/{latest_id}/download"
    )
    assert downloaded.status_code == 200
    assert downloaded.content == PDF + b"-v2"
    assert len(list(storage.root.glob("*.pdf"))) == 2


def test_upload_rejects_invalid_file_and_does_not_leave_file_or_row(api_context) -> None:
    client, _engine, storage = api_context
    company = create_company(client)
    response = upload_document(client, company["id"], content=b"not a PDF")
    assert response.status_code == 415

    listing = client.get(f"/api/companies/{company['id']}/documents").json()
    assert listing["total"] == 0
    assert list(storage.root.glob("*.pdf")) == []


def test_expiry_before_issue_date_is_rejected(api_context) -> None:
    client, _engine, _storage = api_context
    company = create_company(client)
    response = upload_document(
        client,
        company["id"],
        extra={"issue_date": "2027-01-01", "expiry_date": "2026-12-31"},
    )
    assert response.status_code == 422


def test_archived_company_and_document_cannot_receive_new_uploads(api_context) -> None:
    client, _engine, _storage = api_context
    company = create_company(client)
    created = upload_document(client, company["id"])
    document_id = created.json()["id"]

    company_archive = client.patch(f"/api/companies/{company['id']}", json={"is_archived": True})
    assert company_archive.status_code == 200
    blocked_company = upload_document(client, company["id"])
    assert blocked_company.status_code == 409

    client.patch(f"/api/companies/{company['id']}", json={"is_archived": False})
    doc_archive = client.patch(
        f"/api/companies/{company['id']}/documents/{document_id}",
        json={"is_archived": True},
    )
    assert doc_archive.status_code == 200
    blocked_doc = upload_version(client, company["id"], document_id)
    assert blocked_doc.status_code == 422


def test_company_document_archival_filter_and_cross_company_isolation(api_context) -> None:
    client, _engine, _storage = api_context
    first_company = create_company(client, "First Company Limited")
    second_company = create_company(client, "Second Company Limited")
    created = upload_document(client, first_company["id"])
    document_id = created.json()["id"]

    wrong_company = client.get(
        f"/api/companies/{second_company['id']}/documents/{document_id}/versions"
    )
    assert wrong_company.status_code == 404

    archived = client.patch(
        f"/api/companies/{first_company['id']}/documents/{document_id}",
        json={"is_archived": True},
    )
    assert archived.status_code == 200
    assert client.get(f"/api/companies/{first_company['id']}/documents").json()["total"] == 0
    archived_listing = client.get(
        f"/api/companies/{first_company['id']}/documents",
        params={"include_archived": "true"},
    ).json()
    assert archived_listing["total"] == 1
    # Removing a document from the active library is archival, not hard delete:
    # its uploaded version remains available as history.
    archived_document = archived_listing["items"][0]
    assert archived_document["is_archived"] is True
    assert archived_document["current_version"]["id"] == created.json()["current_version"]["id"]
    assert archived_document["version_count"] == 1


def test_update_version_metadata_and_reject_invalid_date_change(api_context) -> None:
    client, _engine, _storage = api_context
    company = create_company(client)
    created = upload_document(
        client,
        company["id"],
        extra={"issue_date": "2026-01-01", "expiry_date": "2026-12-31"},
    )
    version_id = created.json()["current_version"]["id"]
    document_id = created.json()["id"]

    updated = client.patch(
        f"/api/companies/{company['id']}/documents/{document_id}/versions/{version_id}",
        json={"financial_year": "2026-27", "issuer": "Updated Issuer"},
    )
    assert updated.status_code == 200
    assert updated.json()["financial_year"] == "2026-27"

    invalid = client.patch(
        f"/api/companies/{company['id']}/documents/{document_id}/versions/{version_id}",
        json={"expiry_date": "2025-12-31"},
    )
    assert invalid.status_code == 422


def test_unknown_company_and_document_return_404(api_context) -> None:
    client, _engine, _storage = api_context
    unknown = "00000000-0000-0000-0000-000000000001"
    assert client.get(f"/api/companies/{unknown}").status_code == 404
    assert client.get(f"/api/companies/{unknown}/documents").status_code == 404
    company = create_company(client)
    assert client.get(f"/api/companies/{company['id']}/documents/{unknown}/versions").status_code == 404


def test_company_models_enforce_relations_and_version_constraints() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_fk(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        company = Company(legal_name="Model Test Company")
        document = CompanyDocument(
            company=company,
            title="Audited Statements",
            category=CompanyDocumentCategory.AUDITED_FINANCIAL_STATEMENTS,
        )
        version = CompanyDocumentVersion(
            document=document,
            version_number=1,
            original_filename="accounts.pdf",
            storage_key="test-accounts.pdf",
            extension=".pdf",
            content_type="application/pdf",
            size_bytes=10,
            issue_date=date(2026, 4, 1),
            expiry_date=date(2027, 3, 31),
        )
        session.add(company)
        session.commit()
        assert document.current_version is version
        assert document.version_count == 1
        assert isinstance(company.id, UUID)

        invalid = CompanyDocumentVersion(
            document=document,
            version_number=2,
            original_filename="bad.pdf",
            storage_key="bad.pdf",
            extension=".pdf",
            content_type="application/pdf",
            size_bytes=10,
            issue_date=date(2027, 1, 1),
            expiry_date=date(2026, 1, 1),
        )
        session.add(invalid)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        document_id = document.id
        session.delete(company)
        session.commit()
        assert session.scalar(select(CompanyDocument).where(CompanyDocument.id == document_id)) is None


def test_company_documents_migration_upgrade_and_downgrade_isolated_sqlite() -> None:
    migration_path = Path(__file__).parents[1] / "migrations" / "versions"
    modules = []
    for filename in (
        "0001_create_tenders_and_documents.py",
        "0002_create_document_processing_tables.py",
        "0003_create_company_documents.py",
        "0004_company_document_processing.py",
        "0005_tender_evidence_matching.py",
        "0006_evidence_matching_v2.py",
    ):
        spec = importlib.util.spec_from_file_location(filename[:-3], migration_path / filename)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            modules[0].upgrade()
            modules[1].upgrade()
            modules[2].upgrade()
            modules[3].upgrade()
            modules[4].upgrade()
            modules[5].upgrade()
            tables = set(inspect(connection).get_table_names())
            assert {
                "companies",
                "company_documents",
                "company_document_versions",
                "tender_evidence_matching_runs",
            } <= tables
            run_columns = {column["name"] for column in inspect(connection).get_columns("document_processing_runs")}
            item_columns = {column["name"] for column in inspect(connection).get_columns("document_processing_run_items")}
            assert "company_id" in run_columns
            assert "company_document_version_id" in item_columns
            match_columns = {column["name"] for column in inspect(connection).get_columns("tender_evidence_matching_runs")}
            assert {"latest_version_count", "documents_without_versions_count", "non_company_evidence_count"} <= match_columns
            modules[5].downgrade()
            modules[4].downgrade()
            modules[3].downgrade()
            modules[2].downgrade()
            remaining = set(inspect(connection).get_table_names())
            assert "tenders" in remaining and "tender_documents" in remaining
            assert not {
                "companies",
                "company_documents",
                "company_document_versions",
                "tender_evidence_matching_runs",
            } & remaining
    engine.dispose()


def test_placeholder_metadata_is_normalized_to_null_on_upload_and_update(api_context) -> None:
    client, _engine, _storage = api_context
    company = create_company(client)
    created = upload_document(
        client,
        company["id"],
        extra={
            "financial_year": "string",
            "issuer": "string",
            "reference_number": "string",
            "notes": "N/A",
        },
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]
    version_id = created.json()["current_version"]["id"]
    assert created.json()["current_version"]["financial_year"] is None
    assert created.json()["current_version"]["issuer"] is None
    assert created.json()["current_version"]["reference_number"] is None
    assert created.json()["current_version"]["notes"] is None

    updated = client.patch(
        f"/api/companies/{company['id']}/documents/{document_id}/versions/{version_id}",
        json={"financial_year": "string", "reference_number": "not provided"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["financial_year"] is None
    assert updated.json()["reference_number"] is None
