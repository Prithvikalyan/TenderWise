from collections.abc import Generator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import DateTime, create_engine, delete, event, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    DocumentCategory,
    DocumentExtractionUnit,
    DocumentProcessingRun,
    DocumentProcessingRunItem,
    Tender,
    TenderDocument,
    TenderStatus,
)
from app.models.tender import utc_now


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        yield database_session
    Base.metadata.drop_all(engine)
    engine.dispose()


def make_document(tender: Tender, filename: str, category: DocumentCategory) -> TenderDocument:
    return TenderDocument(
        tender=tender,
        original_filename=filename,
        storage_key=str(uuid4()),
        extension=".pdf",
        content_type="application/pdf",
        size_bytes=1024,
        category=category,
    )


def test_create_tender_without_documents(session: Session) -> None:
    tender = Tender(name="Road maintenance tender")
    session.add(tender)
    session.commit()
    session.refresh(tender)

    assert isinstance(tender.id, UUID)
    assert tender.name == "Road maintenance tender"
    assert tender.status is TenderStatus.DRAFT
    assert tender.documents == []
    assert isinstance(Tender.__table__.c.created_at.type, DateTime)
    assert Tender.__table__.c.created_at.type.timezone is True
    assert isinstance(Tender.__table__.c.updated_at.type, DateTime)
    assert Tender.__table__.c.updated_at.type.timezone is True
    assert utc_now().utcoffset().total_seconds() == 0


def test_create_tender_with_multiple_documents(session: Session) -> None:
    tender = Tender(name="Bridge construction tender")
    tender.documents.extend(
        [
            make_document(tender, "main.pdf", DocumentCategory.MAIN_TENDER),
            make_document(tender, "amendment.pdf", DocumentCategory.CORRIGENDUM_AMENDMENT),
        ]
    )
    session.add(tender)
    session.commit()
    session.refresh(tender)

    assert len(tender.documents) == 2
    assert all(document.tender is tender for document in tender.documents)
    assert {document.category for document in tender.documents} == {
        DocumentCategory.MAIN_TENDER,
        DocumentCategory.CORRIGENDUM_AMENDMENT,
    }


def test_database_cascades_document_metadata_on_tender_delete(session: Session) -> None:
    tender = Tender(name="Water supply tender")
    tender.documents.append(make_document(tender, "specification.pdf", DocumentCategory.TECHNICAL_SPECIFICATION))
    session.add(tender)
    session.commit()
    tender_id = tender.id

    session.execute(delete(Tender).where(Tender.id == tender_id))
    session.commit()

    remaining_documents = session.scalar(select(func.count()).select_from(TenderDocument))
    assert remaining_documents == 0


def test_tender_document_requires_parent(session: Session) -> None:
    document = TenderDocument(
        tender_id=UUID("00000000-0000-0000-0000-000000000001"),
        original_filename="orphan.pdf",
        storage_key="orphan-storage-key",
        extension=".pdf",
        content_type="application/pdf",
        size_bytes=1,
        category=DocumentCategory.OTHER,
    )
    session.add(document)

    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def make_processing_graph(session: Session):
    tender = Tender(name="Processing test tender")
    document = make_document(tender, "source.pdf", DocumentCategory.MAIN_TENDER)
    run = DocumentProcessingRun(tender=tender, requested_document_count=1)
    item = DocumentProcessingRunItem(run=run, document=document)
    session.add(tender)
    session.flush()
    return tender, document, run, item


def test_create_processing_run_item_and_extraction_unit(session: Session) -> None:
    tender, document, run, item = make_processing_graph(session)
    unit = DocumentExtractionUnit(
        run_item=item,
        unit_index=0,
        unit_type="text",
        source_ref={"page": 1},
        content_text="Tender text",
    )
    session.add(unit)
    session.commit()

    assert run.status == "pending"
    assert run.requested_document_count == 1
    assert item.status == "pending"
    assert item.attempt_count == 0
    assert item.max_attempts == 3
    assert unit.metadata_json == {}
    assert tender.processing_runs == [run]
    assert run.items == [item]
    assert item.extraction_units == [unit]
    assert document.processing_run_items == [item]


@pytest.mark.parametrize("status", ["pending", "processing", "completed", "partially_completed", "failed"])
def test_processing_run_status_values_are_accepted(session: Session, status: str) -> None:
    _tender, _document, run, item = make_processing_graph(session)
    run.status = status
    session.commit()


@pytest.mark.parametrize("status", ["pending", "processing", "completed", "failed"])
def test_processing_item_status_values_are_accepted(session: Session, status: str) -> None:
    _tender, _document, _run, item = make_processing_graph(session)
    item.status = status
    session.commit()


def test_partially_completed_is_not_a_processing_item_status(session: Session) -> None:
    _tender, _document, _run, item = make_processing_graph(session)
    item.status = "partially_completed"
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_invalid_processing_status_is_rejected(session: Session) -> None:
    _tender, _document, run, _item = make_processing_graph(session)
    run.status = "queued"
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


@pytest.mark.parametrize("unit_type", ["text", "table", "ocr_text"])
def test_extraction_unit_types_are_accepted(session: Session, unit_type: str) -> None:
    _tender, _document, _run, item = make_processing_graph(session)
    values = {"table_data": [["A"]]} if unit_type == "table" else {"content_text": "content"}
    session.add(DocumentExtractionUnit(
        run_item=item, unit_index=0, unit_type=unit_type, source_ref={}, **values
    ))
    session.commit()


def test_invalid_extraction_unit_type_is_rejected(session: Session) -> None:
    _tender, _document, _run, item = make_processing_graph(session)
    session.add(DocumentExtractionUnit(
        run_item=item, unit_index=0, unit_type="binary", source_ref={}, content_text="x"
    ))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


@pytest.mark.parametrize(
    "unit_type,kwargs",
    [("table", {"content_text": "flattened"}), ("text", {"table_data": [["cell"]]}),
     ("ocr_text", {"table_data": [["cell"]]} )],
)
def test_extraction_unit_content_constraints(session: Session, unit_type: str, kwargs: dict) -> None:
    _tender, _document, _run, item = make_processing_graph(session)
    session.add(DocumentExtractionUnit(
        run_item=item, unit_index=0, unit_type=unit_type, source_ref={}, **kwargs
    ))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_item_counters_and_failure_class_constraints(session: Session) -> None:
    _tender, _document, _run, item = make_processing_graph(session)
    item_id = item.id
    session.commit()

    item.attempt_count = -1
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    item = session.get(DocumentProcessingRunItem, item_id)
    assert item is not None
    item.failure_class = "unknown"
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_unique_run_document_and_item_unit_index_constraints(session: Session) -> None:
    _tender, document, run, item = make_processing_graph(session)
    run_id = run.id
    session.commit()

    session.add(DocumentProcessingRunItem(run=run, document=document))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    item = session.scalar(select(DocumentProcessingRunItem).where(DocumentProcessingRunItem.run_id == run_id))
    assert item is not None
    session.add_all([
        DocumentExtractionUnit(run_item=item, unit_index=4, unit_type="text", source_ref={}, content_text="a"),
        DocumentExtractionUnit(run_item=item, unit_index=4, unit_type="text", source_ref={}, content_text="b"),
    ])
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_document_delete_sets_processing_item_document_id_null(session: Session) -> None:
    _tender, document, _run, item = make_processing_graph(session)
    session.flush()
    session.delete(document)
    session.commit()
    session.refresh(item)
    assert item.document_id is None


def test_run_delete_cascades_to_items_and_units(session: Session) -> None:
    _tender, _document, run, item = make_processing_graph(session)
    unit = DocumentExtractionUnit(
        run_item=item, unit_index=0, unit_type="text", source_ref={}, content_text="text"
    )
    session.add(unit)
    session.commit()
    run_id = run.id

    session.delete(run)
    session.commit()

    assert session.scalar(select(func.count()).select_from(DocumentProcessingRun).where(DocumentProcessingRun.id == run_id)) == 0
    assert session.scalar(select(func.count()).select_from(DocumentProcessingRunItem)) == 0
    assert session.scalar(select(func.count()).select_from(DocumentExtractionUnit)) == 0


def test_item_delete_cascades_to_units(session: Session) -> None:
    _tender, _document, _run, item = make_processing_graph(session)
    unit = DocumentExtractionUnit(
        run_item=item, unit_index=0, unit_type="text", source_ref={}, content_text="text"
    )
    session.add(unit)
    session.commit()
    item_id = item.id

    session.delete(item)
    session.commit()

    assert session.scalar(select(func.count()).select_from(DocumentProcessingRunItem).where(DocumentProcessingRunItem.id == item_id)) == 0
    assert session.scalar(select(func.count()).select_from(DocumentExtractionUnit)) == 0


def test_tender_delete_cascades_processing_graph(session: Session) -> None:
    tender, _document, run, item = make_processing_graph(session)
    session.add(DocumentExtractionUnit(
        run_item=item, unit_index=0, unit_type="text", source_ref={}, content_text="text"
    ))
    session.commit()
    tender_id = tender.id

    session.execute(delete(Tender).where(Tender.id == tender_id))
    session.commit()

    assert session.scalar(select(func.count()).select_from(DocumentProcessingRun)) == 0
    assert session.scalar(select(func.count()).select_from(DocumentProcessingRunItem)) == 0
    assert session.scalar(select(func.count()).select_from(DocumentExtractionUnit)) == 0


def test_phase_two_migration_upgrade_and_downgrade_isolated_sqlite() -> None:
    """Exercise both revisions on a private in-memory database, never the configured DB."""
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration_path = Path(__file__).parents[1] / "migrations" / "versions"
    modules = []
    for filename in ("0001_create_tenders_and_documents.py", "0002_create_document_processing_tables.py"):
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
            tables = set(inspect(connection).get_table_names())
            assert {
                "document_processing_runs",
                "document_processing_run_items",
                "document_extraction_units",
            } <= tables
            modules[1].downgrade()
            remaining = set(inspect(connection).get_table_names())
            assert "tenders" in remaining and "tender_documents" in remaining
            assert not {
                "document_processing_runs",
                "document_processing_run_items",
                "document_extraction_units",
            } & remaining
    engine.dispose()
