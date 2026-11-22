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
from app.models import (
    DocumentCategory,
    DocumentProcessingRun,
    DocumentProcessingRunItem,
    DocumentExtractionUnit,
    Tender,
    TenderDocument,
)
from app.services import processing as processing_service


@pytest.fixture
def api_context() -> Generator[tuple[TestClient, Engine], None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)

    def override_session():
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


def create_tender(client: TestClient, name: str = "Processing tender") -> dict:
    response = client.post("/api/tenders", json={"name": name})
    assert response.status_code == 201
    return response.json()


def add_documents(engine: Engine, tender_id: UUID, count: int) -> list[UUID]:
    with Session(engine) as session:
        documents = [
            TenderDocument(
                tender_id=tender_id,
                original_filename=f"document-{index}.pdf",
                storage_key=f"processing-test-{tender_id}-{index}",
                extension=".pdf",
                content_type="application/pdf",
                size_bytes=10,
                category=DocumentCategory.MAIN_TENDER,
            )
            for index in range(count)
        ]
        session.add_all(documents)
        session.commit()
        return [document.id for document in documents]


def start_run(client: TestClient, tender_id: UUID, document_ids: list[UUID]) -> dict:
    response = client.post(
        f"/api/tenders/{tender_id}/processing-runs",
        json={"document_ids": [str(document_id) for document_id in document_ids]},
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_create_run_for_one_document_has_pending_defaults(api_context) -> None:
    client, engine = api_context
    tender = create_tender(client)
    document_id = add_documents(engine, UUID(tender["id"]), 1)[0]

    result = start_run(client, UUID(tender["id"]), [document_id])

    assert result["status"] == "pending"
    assert result["requested_document_count"] == 1
    assert result["started_at"] is None
    assert result["completed_at"] is None
    assert result["created_at"] and result["updated_at"]
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["document_id"] == str(document_id)
    assert item["status"] == "pending"
    assert item["attempt_count"] == 0
    assert item["max_attempts"] == 3


def test_create_run_for_multiple_documents_is_atomic(api_context) -> None:
    client, engine = api_context
    tender = create_tender(client)
    document_ids = add_documents(engine, UUID(tender["id"]), 3)

    result = start_run(client, UUID(tender["id"]), document_ids)

    assert result["requested_document_count"] == 3
    assert {UUID(item["document_id"]) for item in result["items"]} == set(document_ids)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DocumentProcessingRun)) == 1
        assert session.scalar(select(func.count()).select_from(DocumentProcessingRunItem)) == 3


@pytest.mark.parametrize(
    "ids",
    [[], [UUID(int=index) for index in range(1, 22)]],
    ids=["empty", "over-20"],
)
def test_create_run_rejects_invalid_selection_size(api_context, ids: list[UUID]) -> None:
    client, _engine = api_context
    tender = create_tender(client)
    response = client.post(
        f"/api/tenders/{tender['id']}/processing-runs",
        json={"document_ids": [str(value) for value in ids]},
    )
    assert response.status_code == 422


def test_create_run_rejects_duplicate_and_malformed_document_ids(api_context) -> None:
    client, _engine = api_context
    tender = create_tender(client)
    document_id = str(UUID(int=1))
    base = f"/api/tenders/{tender['id']}/processing-runs"

    assert client.post(base, json={"document_ids": [document_id, document_id]}).status_code == 422
    assert client.post(base, json={"document_ids": ["not-a-uuid"]}).status_code == 422


def test_create_run_rejects_missing_foreign_document_and_missing_tender(api_context) -> None:
    client, engine = api_context
    tender_a = create_tender(client, "Tender A")
    tender_b = create_tender(client, "Tender B")
    foreign_document_id = add_documents(engine, UUID(tender_b["id"]), 1)[0]
    nonexistent_id = UUID(int=12345)

    foreign = client.post(
        f"/api/tenders/{tender_a['id']}/processing-runs",
        json={"document_ids": [str(foreign_document_id)]},
    )
    missing = client.post(
        f"/api/tenders/{tender_a['id']}/processing-runs",
        json={"document_ids": [str(nonexistent_id)]},
    )
    missing_tender = client.post(
        f"/api/tenders/{UUID(int=555)}/processing-runs",
        json={"document_ids": [str(nonexistent_id)]},
    )
    assert foreign.status_code == missing.status_code == missing_tender.status_code == 404
    assert foreign.json() == missing.json()


def test_run_creation_rolls_back_if_item_flush_fails(api_context) -> None:
    _client, engine = api_context
    tender_id = UUID(create_tender(_client)["id"])
    document_ids = add_documents(engine, tender_id, 2)

    def fail_item_flush(session, _flush_context, _instances) -> None:
        if any(isinstance(instance, DocumentProcessingRunItem) for instance in session.new):
            raise RuntimeError("simulated item insert failure")

    event.listen(Session, "before_flush", fail_item_flush)
    try:
        with Session(engine) as session, pytest.raises(RuntimeError, match="simulated item insert failure"):
            processing_service.create_processing_run(session, tender_id, document_ids)
    finally:
        event.remove(Session, "before_flush", fail_item_flush)

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DocumentProcessingRun)) == 0
        assert session.scalar(select(func.count()).select_from(DocumentProcessingRunItem)) == 0


@pytest.mark.parametrize(
    "statuses,expected",
    [
        (["pending", "pending"], "pending"),
        (["processing", "pending"], "processing"),
        (["completed", "pending"], "processing"),
        (["failed", "pending"], "processing"),
        (["completed", "completed"], "completed"),
        (["completed", "failed"], "partially_completed"),
        (["failed", "failed"], "failed"),
    ],
)
def test_calculate_run_status(statuses: list[str], expected: str) -> None:
    assert processing_service.calculate_run_status(statuses) == expected


@pytest.mark.parametrize("statuses", [[], ["partially_completed"], ["queued"]])
def test_calculate_run_status_rejects_empty_or_invalid_item_state(statuses: list[str]) -> None:
    with pytest.raises(processing_service.InvalidRunStateError):
        processing_service.calculate_run_status(statuses)


def test_aggregate_run_status_persists_inside_callers_transaction(api_context) -> None:
    client, engine = api_context
    tender_id = UUID(create_tender(client)["id"])
    document_ids = add_documents(engine, tender_id, 2)
    run_data = start_run(client, tender_id, document_ids)

    with Session(engine, autoflush=False) as session:
        items = list(session.scalars(select(DocumentProcessingRunItem).where(
            DocumentProcessingRunItem.run_id == UUID(run_data["id"])
        )))
        items[0].status = "completed"
        assert processing_service.aggregate_run_status(session, UUID(run_data["id"])) == "processing"
        session.commit()
        run = session.get(DocumentProcessingRun, UUID(run_data["id"]))
        assert run is not None and run.status == "processing"


@pytest.mark.parametrize(
    "statuses,expected",
    [
        (["completed", "pending"], "processing"),
        (["completed", "completed"], "completed"),
        (["completed", "failed"], "partially_completed"),
    ],
)
def test_aggregate_run_status_flushes_without_committing(
    api_context, statuses: list[str], expected: str
) -> None:
    client, engine = api_context
    tender_id = UUID(create_tender(client)["id"])
    document_ids = add_documents(engine, tender_id, len(statuses))
    run_data = start_run(client, tender_id, document_ids)
    run_id = UUID(run_data["id"])

    with Session(engine, autoflush=False) as session:
        items = list(session.scalars(select(DocumentProcessingRunItem).where(
            DocumentProcessingRunItem.run_id == run_id
        )))
        for item, item_status in zip(items, statuses):
            item.status = item_status

        # No caller flush: aggregation must flush after acquiring the run lock.
        assert processing_service.aggregate_run_status(session, run_id) == expected
        assert session.in_transaction()
        session.rollback()

    # The service must leave transaction ownership to its caller.
    with Session(engine) as session:
        run = session.get(DocumentProcessingRun, run_id)
        persisted_items = list(session.scalars(select(DocumentProcessingRunItem).where(
            DocumentProcessingRunItem.run_id == run_id
        )))
        assert run is not None and run.status == "pending"
        assert all(item.status == "pending" for item in persisted_items)


def test_run_history_pagination_order_and_tender_scope(api_context) -> None:
    client, engine = api_context
    tender = create_tender(client)
    other_tender = create_tender(client, "Other tender")
    document_id = add_documents(engine, UUID(tender["id"]), 1)[0]
    run_ids = [UUID(start_run(client, UUID(tender["id"]), [document_id])["id"]) for _ in range(3)]
    other_document = add_documents(engine, UUID(other_tender["id"]), 1)[0]
    start_run(client, UUID(other_tender["id"]), [other_document])

    fixed_time = datetime(2026, 1, 1, tzinfo=UTC)
    with Session(engine) as session:
        session.execute(update(DocumentProcessingRun).where(
            DocumentProcessingRun.id.in_(run_ids)
        ).values(created_at=fixed_time))
        session.commit()

    first = client.get(f"/api/tenders/{tender['id']}/processing-runs", params={"page": 1, "limit": 2})
    second = client.get(f"/api/tenders/{tender['id']}/processing-runs", params={"page": 2, "limit": 2})
    assert first.status_code == second.status_code == 200
    assert first.json()["total"] == 3
    assert first.json()["offset"] == 0 and second.json()["offset"] == 2
    assert [UUID(item["id"]) for item in first.json()["items"]] == sorted(run_ids, reverse=True)[:2]
    assert [UUID(item["id"]) for item in second.json()["items"]] == sorted(run_ids, reverse=True)[2:]
    assert client.get(f"/api/tenders/{tender['id']}/processing-runs", params={"limit": 101}).status_code == 422
    assert client.get(f"/api/tenders/{UUID(int=789)}/processing-runs").status_code == 404


def test_run_detail_counts_pagination_and_safe_fields(api_context) -> None:
    client, engine = api_context
    tender = create_tender(client)
    document_ids = add_documents(engine, UUID(tender["id"]), 3)
    run = start_run(client, UUID(tender["id"]), document_ids)
    run_id = UUID(run["id"])
    with Session(engine) as session:
        items = list(session.scalars(select(DocumentProcessingRunItem).where(
            DocumentProcessingRunItem.run_id == run_id
        ).order_by(DocumentProcessingRunItem.id)))
        items[0].status = "completed"
        items[1].status = "failed"
        items[1].worker_id = "must-not-be-returned"
        items[1].fencing_token = 7
        items[1].lease_expires_at = datetime.now(UTC)
        session.commit()

    response = client.get(
        f"/api/tenders/{tender['id']}/processing-runs/{run['id']}",
        params={"page": 1, "limit": 1},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "processing"  # terminal items plus a pending item
    assert body["item_counts"] == {"pending": 1, "processing": 0, "completed": 1, "failed": 1}
    assert body["total_items"] == 3 and len(body["items"]) == 1
    serialized = response.text
    assert "worker_id" not in serialized
    assert "fencing_token" not in serialized
    assert "lease_expires_at" not in serialized
    assert client.get(f"/api/tenders/{UUID(int=890)}/processing-runs/{run['id']}").status_code == 404
    assert client.get(f"/api/tenders/{tender['id']}/processing-runs/{UUID(int=891)}").status_code == 404
    assert client.get(
        f"/api/tenders/{tender['id']}/processing-runs/{run['id']}", params={"page": 0}
    ).status_code == 422


def test_document_processing_history_is_paginated_ordered_and_scoped(api_context) -> None:
    client, engine = api_context
    tender = create_tender(client)
    other_tender = create_tender(client, "Other tender")
    document_id = add_documents(engine, UUID(tender["id"]), 1)[0]
    run_ids = [UUID(start_run(client, UUID(tender["id"]), [document_id])["id"]) for _ in range(3)]
    foreign_document_id = add_documents(engine, UUID(other_tender["id"]), 1)[0]

    fixed_time = datetime(2026, 2, 1, tzinfo=UTC)
    with Session(engine) as session:
        session.execute(update(DocumentProcessingRun).where(
            DocumentProcessingRun.id.in_(run_ids)
        ).values(created_at=fixed_time))
        session.commit()

    base = f"/api/tenders/{tender['id']}/documents/{document_id}/processing-runs"
    first = client.get(base, params={"page": 1, "limit": 2})
    second = client.get(base, params={"page": 2, "limit": 2})
    assert first.status_code == second.status_code == 200
    assert first.json()["total"] == 3
    assert [UUID(item["run_id"]) for item in first.json()["items"]] == sorted(run_ids, reverse=True)[:2]
    assert [UUID(item["run_id"]) for item in second.json()["items"]] == sorted(run_ids, reverse=True)[2:]
    assert first.json()["items"][0]["document_id"] == str(document_id)
    assert "worker_id" not in first.text and "fencing_token" not in first.text
    assert client.get(
        f"/api/tenders/{tender['id']}/documents/{foreign_document_id}/processing-runs"
    ).status_code == 404
    assert client.get(
        f"/api/tenders/{other_tender['id']}/documents/{document_id}/processing-runs"
    ).status_code == 404
    assert client.get(
        f"/api/tenders/{tender['id']}/documents/{UUID(int=892)}/processing-runs"
    ).status_code == 404


def test_pagination_page_boundaries_apply_to_every_endpoint(api_context) -> None:
    client, engine = api_context
    tender = create_tender(client)
    document_id = add_documents(engine, UUID(tender["id"]), 1)[0]
    run = start_run(client, UUID(tender["id"]), [document_id])
    endpoints = [
        f"/api/tenders/{tender['id']}/processing-runs",
        f"/api/tenders/{tender['id']}/processing-runs/{run['id']}",
        f"/api/tenders/{tender['id']}/documents/{document_id}/processing-runs",
    ]

    for endpoint in endpoints:
        assert client.get(endpoint, params={"page": 1}).status_code == 200
        assert client.get(endpoint, params={"page": 2}).status_code == 200
        assert client.get(endpoint, params={"page": 0}).status_code == 422
        assert client.get(endpoint, params={"page": -1}).status_code == 422
        maximum = client.get(endpoint, params={"page": 1_000_000, "limit": 100})
        assert maximum.status_code == 200, maximum.text
        assert maximum.json()["page"] == 1_000_000
        assert client.get(endpoint, params={"page": 1_000_001}).status_code == 422


def test_completed_processing_item_exposes_extracted_text_preview(api_context) -> None:
    client, engine = api_context
    tender_id = UUID(create_tender(client)["id"])
    document_id = add_documents(engine, tender_id, 1)[0]
    run_data = start_run(client, tender_id, [document_id])
    run_id = UUID(run_data["id"])
    item_id = UUID(run_data["items"][0]["id"])

    with Session(engine) as session:
        item = session.get(DocumentProcessingRunItem, item_id)
        assert item is not None
        item.status = "completed"
        item.outcome_code = "extracted"
        session.add(DocumentExtractionUnit(
            run_item_id=item_id,
            unit_index=0,
            unit_type="text",
            source_ref={"page": 1, "block_index": 2, "filename": "document-0.pdf"},
            content_text="The bidder must be legally registered in India.",
            metadata_json={"format": "pdf"},
        ))
        session.commit()

    response = client.get(
        f"/api/tenders/{tender_id}/processing-runs/{run_id}/items/{item_id}/extraction"
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["extraction_unit_count"] == 1
    assert payload["has_extracted_text"] is True
    assert payload["units"][0]["content_text"] == "The bidder must be legally registered in India."
    assert "Page 1" in payload["units"][0]["citation"]


def test_completed_processing_item_with_no_units_reports_empty_extraction(api_context) -> None:
    client, engine = api_context
    tender_id = UUID(create_tender(client)["id"])
    document_id = add_documents(engine, tender_id, 1)[0]
    run_data = start_run(client, tender_id, [document_id])
    run_id = UUID(run_data["id"])
    item_id = UUID(run_data["items"][0]["id"])

    with Session(engine) as session:
        item = session.get(DocumentProcessingRunItem, item_id)
        assert item is not None
        item.status = "completed"
        session.commit()

    response = client.get(
        f"/api/tenders/{tender_id}/processing-runs/{run_id}/items/{item_id}/extraction"
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["extraction_unit_count"] == 0
    assert payload["has_extracted_text"] is False
    assert payload["units"] == []
