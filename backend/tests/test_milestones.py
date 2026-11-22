from collections.abc import Generator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import milestones as milestone_routes
from app.db.base import Base
from app.db.session import get_db_session
from app.main import app
from app.models.milestone import TenderMilestone


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


def create_tender(client: TestClient) -> dict:
    response = client.post("/api/tenders", json={"name": "Milestone workspace", "reference_number": "MW-01"})
    assert response.status_code == 201, response.text
    return response.json()


def test_create_list_update_and_delete_milestone(client: TestClient) -> None:
    tender = create_tender(client)
    create = client.post(
        f"/api/tenders/{tender['id']}/milestones",
        json={
            "title": "Online bid submission closes",
            "category": "submission_deadline",
            "due_date": "2026-12-15",
            "source_reference": "Tender notice, page 8, clause 2.1",
        },
    )
    assert create.status_code == 201, create.text
    item = create.json()
    assert item["tender_name"] == tender["name"]
    assert item["tender_reference"] == "MW-01"
    assert item["is_completed"] is False

    listing = client.get("/api/milestones", params={"from_date": "2026-12-01", "to_date": "2026-12-31"})
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == item["id"]

    completed = client.patch(f"/api/milestones/{item['id']}", json={"is_completed": True})
    assert completed.status_code == 200, completed.text
    assert completed.json()["is_completed"] is True
    assert client.get("/api/milestones", params={"from_date": "2026-12-01", "to_date": "2026-12-31", "include_completed": "false"}).json()["total"] == 0

    removed = client.delete(f"/api/milestones/{item['id']}")
    assert removed.status_code == 204
    assert client.get("/api/milestones", params={"from_date": "2026-12-01", "to_date": "2026-12-31"}).json()["total"] == 0


def test_milestone_validation_and_not_found(client: TestClient) -> None:
    tender = create_tender(client)
    invalid_title = client.post(
        f"/api/tenders/{tender['id']}/milestones",
        json={"title": "   ", "due_date": "2026-12-15"},
    )
    assert invalid_title.status_code == 422

    invalid_category = client.post(
        f"/api/tenders/{tender['id']}/milestones",
        json={"title": "Deadline", "due_date": "2026-12-15", "category": "fake"},
    )
    assert invalid_category.status_code == 422

    assert client.get("/api/milestones", params={"from_date": "2026-12-31", "to_date": "2026-12-01"}).status_code == 422
    assert client.patch("/api/milestones/00000000-0000-0000-0000-000000000001", json={"is_completed": True}).status_code == 404
    assert client.delete("/api/milestones/00000000-0000-0000-0000-000000000001").status_code == 404


def test_tender_delete_cascades_milestones(client: TestClient) -> None:
    tender = create_tender(client)
    created = client.post(
        f"/api/tenders/{tender['id']}/milestones",
        json={"title": "Pre-bid meeting", "category": "pre_bid_meeting", "due_date": "2026-12-10"},
    )
    assert created.status_code == 201
    assert client.delete(f"/api/tenders/{tender['id']}").status_code == 204
    assert client.get("/api/milestones", params={"from_date": "2026-12-01", "to_date": "2026-12-31"}).json()["total"] == 0
