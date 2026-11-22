"""PostgreSQL-only queue tests.

Set TENDERWISE_TEST_POSTGRESQL_URL to the disposable tenderwise_test database.
The fixture creates and drops the application schema in that database.
"""

import hashlib
import os
import threading
import time
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile
import xml.etree.ElementTree as ET

import pytest
import pymupdf
from openpyxl import Workbook
from docx import Document as DocxDocument
from fastapi import UploadFile
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base
from app.models import (
    Company,
    CompanyDocument,
    CompanyDocumentCategory,
    CompanyDocumentVersion,
    DocumentCategory,
    DocumentExtractionUnit,
    DocumentProcessingRun,
    DocumentProcessingRunItem,
    Tender,
    TenderDocument,
)
from app.services.document_storage import DocumentStorage
from app.services.documents import upload_documents
from app.services.source_deletion import delete_source_document, delete_tender_sources
from app.services.processing_handler import ExtractionUnitData, ProcessingFailure
from app.services.pdf_extraction import PdfTextExtractionHandler
from app.services.docx_extraction import DocxTextExtractionHandler
from app.services.processing_handlers import FormatAwareProcessingHandler
from app.services.processing_worker import (
    ClaimedProcessingItem,
    ProcessingWorker,
    WorkerCapacityError,
    acquire_worker_slot,
    claim_next_item,
    persist_result,
    record_failure,
    renew_lease,
    release_worker_slot,
)

TEST_DATABASE_URL = os.getenv("TENDERWISE_TEST_POSTGRESQL_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TENDERWISE_TEST_POSTGRESQL_URL is not configured",
)


@pytest.fixture
def pg_context(tmp_path):
    assert TEST_DATABASE_URL is not None
    if make_url(TEST_DATABASE_URL).database != "tenderwise_test":
        pytest.fail("PostgreSQL worker tests require the disposable tenderwise_test database")
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        pytest.skip("Configured worker integration URL is not PostgreSQL")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, class_=Session, autoflush=False, autocommit=False)
    storage = DocumentStorage(tmp_path / "private-storage")
    settings = Settings(_env_file=None)
    try:
        yield engine, session_factory, storage, settings
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def seed_item(engine: Engine, storage: DocumentStorage, *, status="pending", attempts=0, max_attempts=3, due_at=None):
    now = datetime.now(UTC)
    source_path = storage.root / f"{uuid4().hex}.pdf"
    source_content = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\nstartxref\n0\n%%EOF\n"
    source_path.write_bytes(source_content)
    with Session(engine) as session:
        tender = Tender(name=f"Worker test {uuid4()}")
        document = TenderDocument(
            tender=tender,
            original_filename="fixture.pdf",
            storage_key=source_path.name,
            extension=".pdf",
            content_type="application/pdf",
            size_bytes=len(source_content),
            sha256=hashlib.sha256(source_content).hexdigest(),
            category=DocumentCategory.MAIN_TENDER,
        )
        run = DocumentProcessingRun(tender=tender, requested_document_count=1)
        item = DocumentProcessingRunItem(
            run=run,
            document=document,
            status=status,
            attempt_count=attempts,
            max_attempts=max_attempts,
            next_attempt_at=due_at or now - timedelta(seconds=1),
            lease_expires_at=now - timedelta(seconds=1) if status == "processing" else None,
            worker_id="stale-worker" if status == "processing" else None,
            fencing_token=attempts if status == "processing" else 0,
        )
        session.add(tender)
        session.commit()
        return tender.id, document.id, run.id, item.id, source_path


def test_postgres_pdf_text_extraction_persists_through_worker_transaction(pg_context):
    engine, session_factory, storage, settings = pg_context
    _tender_id, document_id, _run_id, item_id, source_path = seed_item(engine, storage)

    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((50, 72), "PDF integration heading")
    for left, right, labels in (
        (100, 300, [["A", "B"], ["C", "D"]]),
        (350, 550, [["E", "F"], ["G", "H"]]),
    ):
        for x in (left, (left + right) / 2, right):
            page.draw_line((x, 100), (x, 180), color=(0, 0, 0), width=1)
        for y in (100, 140, 180):
            page.draw_line((left, y), (right, y), color=(0, 0, 0), width=1)
        for row_index, row in enumerate(labels):
            for col_index, value in enumerate(row):
                page.insert_text(
                    (left + 5 + col_index * ((right - left) / 2), 125 + row_index * 40),
                    value,
                )
    page = pdf.new_page()
    page.insert_text((72, 72), "Second page content")
    pdf.save(source_path)
    pdf.close()
    content = source_path.read_bytes()
    with Session(engine) as session:
        source = session.get(TenderDocument, document_id)
        assert source is not None
        source.size_bytes = len(content)
        source.sha256 = hashlib.sha256(content).hexdigest()
        session.commit()

    claim = claim_next_item(session_factory, "worker-pdf", settings, storage)
    assert claim is not None and claim.item_id == item_id
    assert claim.original_filename == "fixture.pdf" and claim.extension == ".pdf"
    handler = FormatAwareProcessingHandler(PdfTextExtractionHandler(settings))
    worker = ProcessingWorker(
        session_factory, settings, storage, handler, worker_id=claim.worker_id
    )
    worker._process_claim(claim, threading.Event())

    with Session(engine) as session:
        item = session.get(DocumentProcessingRunItem, item_id)
        stored_units = list(
            session.scalars(
                select(DocumentExtractionUnit)
                .where(DocumentExtractionUnit.run_item_id == item_id)
                .order_by(DocumentExtractionUnit.unit_index)
            ).all()
        )
        assert item is not None and item.status == "completed"
        assert len(stored_units) == 4
        assert [unit.unit_index for unit in stored_units] == [0, 1, 2, 3]
        assert [unit.unit_type for unit in stored_units] == ["text", "table", "table", "text"]
        assert stored_units[0].content_text.strip() == "PDF integration heading"
        assert [unit.table_data["rows"][0]["cells"][0]["text"] for unit in stored_units[1:3]] == [
            "A",
            "E",
        ]
        assert [unit.source_ref["page"] for unit in stored_units] == [1, 1, 1, 2]
        assert [unit.source_ref.get("table_index") for unit in stored_units[1:3]] == [0, 1]
        assert [unit.source_ref.get("block_index") for unit in (stored_units[0], stored_units[3])] == [0, 0]
        assert all(unit.source_ref["document_id"] == str(document_id) for unit in stored_units)


def test_postgres_company_document_version_uses_shared_worker_queue(pg_context):
    engine, session_factory, storage, settings = pg_context
    now = datetime.now(UTC)
    source_path = storage.root / f"{uuid4().hex}.pdf"
    source_content = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\nstartxref\n0\n%%EOF\n"
    source_path.write_bytes(source_content)

    with Session(engine) as session:
        company = Company(legal_name=f"Company worker test {uuid4()}")
        document = CompanyDocument(
            company=company,
            title="GST registration certificate",
            category=CompanyDocumentCategory.GST_CERTIFICATE,
        )
        version = CompanyDocumentVersion(
            document=document,
            version_number=1,
            original_filename="gst-certificate.pdf",
            storage_key=source_path.name,
            extension=".pdf",
            content_type="application/pdf",
            size_bytes=len(source_content),
            sha256=hashlib.sha256(source_content).hexdigest(),
        )
        run = DocumentProcessingRun(
            company=company,
            tender_id=None,
            requested_document_count=1,
        )
        item = DocumentProcessingRunItem(
            run=run,
            company_document_version=version,
            next_attempt_at=now - timedelta(seconds=1),
        )
        session.add(company)
        session.commit()
        company_id = company.id
        company_document_id = document.id
        version_id = version.id
        run_item_id = item.id

    claim = claim_next_item(session_factory, "worker-company", settings, storage)
    assert claim is not None and claim.item_id == run_item_id
    assert claim.document_id is None
    assert claim.tender_id is None
    assert claim.company_id == company_id
    assert claim.company_document_id == company_document_id
    assert claim.company_document_version_id == version_id

    class CompanyEvidenceHandler:
        def process(self, context):
            return [
                ExtractionUnitData(
                    unit_type="text",
                    source_ref={
                        "document_id": str(context.document_id),
                        "filename": context.original_filename,
                        "page": 1,
                        "block_index": 0,
                    },
                    content_text="GST registration proof",
                    metadata={"format": "pdf", "extractor": "test"},
                )
            ]

    worker = ProcessingWorker(
        session_factory, settings, storage, CompanyEvidenceHandler(), worker_id=claim.worker_id
    )
    worker._process_claim(claim, threading.Event())

    with Session(engine) as session:
        completed_item = session.get(DocumentProcessingRunItem, run_item_id)
        units = list(
            session.scalars(
                select(DocumentExtractionUnit).where(
                    DocumentExtractionUnit.run_item_id == run_item_id
                )
            ).all()
        )
        assert completed_item is not None and completed_item.status == "completed"
        assert len(units) == 1
        assert units[0].content_text == "GST registration proof"
        assert units[0].source_ref["document_id"] == str(company_document_id)
        assert units[0].source_ref["company_document_version_id"] == str(version_id)
        assert units[0].source_ref["source_scope"] == "company"


def _prepare_docx_source(engine, document_id, source_path):
    document = DocxDocument()
    document.add_heading("Eligibility", level=2)
    document.add_paragraph("Must submit proof")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Document"
    table.cell(0, 1).text = "Required"
    table.cell(1, 0).text = "Registration"
    table.cell(1, 1).text = "Yes"
    document.add_paragraph("尾部文本")
    document.save(source_path)
    content = source_path.read_bytes()
    with Session(engine) as session:
        source = session.get(TenderDocument, document_id)
        assert source is not None
        source.original_filename = "fixture.docx"
        source.extension = ".docx"
        source.content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        source.size_bytes = len(content)
        source.sha256 = hashlib.sha256(content).hexdigest()
        session.commit()


def test_postgres_docx_text_and_tables_persist_in_worker_order(pg_context):
    engine, session_factory, storage, settings = pg_context
    _tender_id, document_id, _run_id, item_id, source_path = seed_item(engine, storage)
    _prepare_docx_source(engine, document_id, source_path)
    claim = claim_next_item(session_factory, "worker-docx", settings, storage)
    assert claim is not None and claim.item_id == item_id
    handler = FormatAwareProcessingHandler(
        PdfTextExtractionHandler(settings), DocxTextExtractionHandler(settings)
    )
    worker = ProcessingWorker(session_factory, settings, storage, handler, worker_id=claim.worker_id)
    worker._process_claim(claim, threading.Event())

    with Session(engine) as session:
        item = session.get(DocumentProcessingRunItem, item_id)
        units = list(session.scalars(
            select(DocumentExtractionUnit)
            .where(DocumentExtractionUnit.run_item_id == item_id)
            .order_by(DocumentExtractionUnit.unit_index)
        ).all())
        assert item is not None and item.status == "completed"
        assert [unit.unit_index for unit in units] == [0, 1, 2, 3]
        assert [unit.unit_type for unit in units] == ["text", "text", "table", "text"]
        assert [unit.content_text for unit in (units[0], units[1], units[3])] == [
            "Eligibility", "Must submit proof", "尾部文本"
        ]
        assert units[0].source_ref["heading_level"] == 2
        assert units[2].table_data["rows"][0]["cells"][0]["text"] == "Document"
        assert units[2].source_ref["table_index"] == 0
        assert [unit.source_ref["body_order_index"] for unit in units] == [0, 1, 2, 3]
        assert all(unit.source_ref["document_id"] == str(document_id) for unit in units)


def test_postgres_docx_late_extraction_failure_persists_zero_units(pg_context, monkeypatch):
    engine, session_factory, storage, settings = pg_context
    _tender_id, document_id, _run_id, item_id, source_path = seed_item(engine, storage)
    _prepare_docx_source(engine, document_id, source_path)
    claim = claim_next_item(session_factory, "worker-docx-failure", settings, storage)
    assert claim is not None and claim.item_id == item_id
    real_document = DocxDocument

    class BrokenBody:
        def iterchildren(self):
            yield from real_document(source_path).element.body.iterchildren()
            raise ValueError("private parser detail")

    class BrokenDocument:
        element = type("Element", (), {"body": BrokenBody()})()

    monkeypatch.setattr("app.services.docx_extraction.Document", lambda _path: BrokenDocument())
    handler = FormatAwareProcessingHandler(
        PdfTextExtractionHandler(settings), DocxTextExtractionHandler(settings)
    )
    worker = ProcessingWorker(session_factory, settings, storage, handler, worker_id=claim.worker_id)
    worker._process_claim(claim, threading.Event())

    with Session(engine) as session:
        item = session.get(DocumentProcessingRunItem, item_id)
        count = session.query(DocumentExtractionUnit).filter_by(run_item_id=item_id).count()
        assert item is not None and item.status == "failed"
        assert item.outcome_code == "malformed_file"
        assert count == 0


def _prepare_tabular_source(engine, document_id, source_path, extension, content_type):
    content = source_path.read_bytes()
    with Session(engine) as session:
        source = session.get(TenderDocument, document_id)
        assert source is not None
        source.original_filename = f"fixture{extension}"
        source.extension = extension
        source.content_type = content_type
        source.size_bytes = len(content)
        source.sha256 = hashlib.sha256(content).hexdigest()
        session.commit()


def _inject_xlsx_cached_value(source_path, coordinate, value):
    temporary = source_path.with_name(f"{source_path.stem}-cached.xlsx")
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(source_path) as source, ZipFile(temporary, "w", ZIP_DEFLATED) as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                root = ET.fromstring(payload)
                cell = root.find(f".//x:c[@r='{coordinate}']", namespace)
                assert cell is not None
                cached = cell.find("x:v", namespace)
                assert cached is not None
                cached.text = str(value)
                payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(info, payload)
    temporary.replace(source_path)


def test_postgres_xlsx_dispatch_and_formula_metadata_persist_through_worker(pg_context):
    engine, session_factory, storage, settings = pg_context
    _tender_id, document_id, _run_id, item_id, source_path = seed_item(engine, storage)
    workbook = Workbook()
    first = workbook.active
    first.title = "BOQ"
    first.append(["Item", "Qty", "Rate", "Amount"])
    first.append(["01", 2, 15.5, "=B2*C2"])
    workbook.create_sheet("Terms")["A2"] = "Rates are in INR"
    workbook.save(source_path)
    _inject_xlsx_cached_value(source_path, "D2", 31)
    _prepare_tabular_source(
        engine,
        document_id,
        source_path,
        ".xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    claim = claim_next_item(session_factory, "worker-xlsx", settings, storage)
    assert claim is not None and claim.item_id == item_id
    handler = FormatAwareProcessingHandler(PdfTextExtractionHandler(settings))
    worker = ProcessingWorker(session_factory, settings, storage, handler, worker_id=claim.worker_id)
    worker._process_claim(claim, threading.Event())

    with Session(engine) as session:
        item = session.get(DocumentProcessingRunItem, item_id)
        units = list(session.scalars(
            select(DocumentExtractionUnit)
            .where(DocumentExtractionUnit.run_item_id == item_id)
            .order_by(DocumentExtractionUnit.unit_index)
        ).all())
        assert item is not None and item.status == "completed"
        assert [unit.unit_index for unit in units] == [0, 1]
        assert [unit.source_ref["worksheet_name"] for unit in units] == ["BOQ", "Terms"]
        assert [unit.source_ref["worksheet_index"] for unit in units] == [0, 1]
        formula = units[0].table_data["rows"][1]["cells"][3]
        assert formula["formula"] == "=B2*C2"
        assert formula["cached_value"] == 31
        assert formula["cached_value_available"] is True
        assert units[0].source_ref["document_id"] == str(document_id)
        assert units[1].table_data["rows"][0]["cells"][0]["coordinate"] == "A2"


def test_postgres_csv_duplicate_headers_and_structured_rows_persist(pg_context):
    engine, session_factory, storage, settings = pg_context
    _tender_id, document_id, _run_id, item_id, source_path = seed_item(engine, storage)
    source_path.write_text("Item,Item,Quantity\nBolt,Bolt type A,4\n", encoding="utf-8")
    _prepare_tabular_source(engine, document_id, source_path, ".csv", "text/csv")

    claim = claim_next_item(session_factory, "worker-csv", settings, storage)
    assert claim is not None and claim.item_id == item_id
    handler = FormatAwareProcessingHandler(PdfTextExtractionHandler(settings))
    worker = ProcessingWorker(session_factory, settings, storage, handler, worker_id=claim.worker_id)
    worker._process_claim(claim, threading.Event())

    with Session(engine) as session:
        item = session.get(DocumentProcessingRunItem, item_id)
        units = list(session.scalars(
            select(DocumentExtractionUnit)
            .where(DocumentExtractionUnit.run_item_id == item_id)
            .order_by(DocumentExtractionUnit.unit_index)
        ).all())
        assert item is not None and item.status == "completed"
        assert len(units) == 1 and units[0].unit_index == 0
        rows = units[0].table_data["rows"]
        assert [cell["value"] for cell in rows[0]["cells"]] == ["Item", "Item", "Quantity"]
        assert [cell["cell_index"] for cell in rows[0]["cells"]] == [0, 1, 2]
        assert rows[1]["cells"][2]["value"] == "4"
        assert units[0].source_ref["document_id"] == str(document_id)
        assert units[0].source_ref["row_end"] == 1


def test_postgres_csv_late_extraction_failure_persists_zero_units(pg_context):
    engine, session_factory, storage, settings = pg_context
    _tender_id, document_id, _run_id, item_id, source_path = seed_item(engine, storage)
    source_path.write_text('First,valid\nSecond,valid\nThird,"unfinished\n', encoding="utf-8")
    _prepare_tabular_source(engine, document_id, source_path, ".csv", "text/csv")

    claim = claim_next_item(session_factory, "worker-csv-failure", settings, storage)
    assert claim is not None and claim.item_id == item_id
    handler = FormatAwareProcessingHandler(PdfTextExtractionHandler(settings))
    worker = ProcessingWorker(session_factory, settings, storage, handler, worker_id=claim.worker_id)
    worker._process_claim(claim, threading.Event())

    with Session(engine) as session:
        item = session.get(DocumentProcessingRunItem, item_id)
        assert item is not None and item.status == "failed"
        assert item.outcome_code == "malformed_file"
        assert session.query(DocumentExtractionUnit).filter_by(run_item_id=item_id).count() == 0


def test_postgres_pdf_extraction_failure_after_first_page_persists_no_units(
    pg_context, monkeypatch
):
    engine, session_factory, storage, settings = pg_context
    _tender_id, document_id, _run_id, item_id, source_path = seed_item(engine, storage)

    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Extracted before later failure")
    pdf.new_page()
    pdf.save(source_path)
    pdf.close()
    content = source_path.read_bytes()
    with Session(engine) as session:
        source = session.get(TenderDocument, document_id)
        assert source is not None
        source.size_bytes = len(content)
        source.sha256 = hashlib.sha256(content).hexdigest()
        session.commit()

    claim = claim_next_item(session_factory, "worker-pdf-failure", settings, storage)
    assert claim is not None and claim.item_id == item_id
    page_calls = []

    class FakePage:
        def __init__(self, page_index):
            self.page_index = page_index
            # Keep overlap suppression disabled in this synthetic page. The
            # second page raises before alignment is inspected.
            self.rotation = 90

        def get_text(self, *_args, **_kwargs):
            page_calls.append(self.page_index)
            if self.page_index == 1:
                raise RuntimeError("private parser detail")
            return [(1.0, 2.0, 3.0, 4.0, "one local unit", 0, 0)]

    class FakeDocument:
        page_count = 2

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def load_page(self, page_index):
            return FakePage(page_index)

    monkeypatch.setattr(
        "app.services.pdf_extraction.pymupdf.open",
        lambda *_args, **_kwargs: FakeDocument(),
    )
    handler = FormatAwareProcessingHandler(PdfTextExtractionHandler(settings))
    worker = ProcessingWorker(
        session_factory, settings, storage, handler, worker_id=claim.worker_id
    )
    worker._process_claim(claim, threading.Event())

    assert page_calls == [0, 1]
    with Session(engine) as session:
        item = session.get(DocumentProcessingRunItem, item_id)
        assert item is not None
        assert item.status == "failed"
        assert item.failure_class == "permanent"
        assert item.outcome_code == "malformed_file"
        assert item.safe_message == "The document is malformed or incomplete."
        assert session.query(DocumentExtractionUnit).filter_by(run_item_id=item_id).count() == 0


def test_postgres_claim_uses_document_lock_before_skip_locked_item(pg_context):
    engine, session_factory, storage, settings = pg_context
    tender_id, _document_id, _run_id, item_id, _path = seed_item(engine, storage)
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        if "FOR KEY SHARE" in statement or "FOR UPDATE SKIP LOCKED" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        claim = claim_next_item(session_factory, "worker-a", settings, storage)
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert claim is not None and claim.item_id == item_id and claim.tender_id == tender_id
    assert len(statements) >= 2
    assert "tender_documents" in statements[0] and "FOR KEY SHARE" in statements[0]
    assert "document_processing_run_items" in statements[1] and "FOR UPDATE SKIP LOCKED" in statements[1]


def test_postgres_claim_skips_future_work_and_reclaims_with_new_fence(pg_context):
    _engine, session_factory, storage, settings = pg_context
    now = datetime.now(UTC)
    _tender, _doc, _run, future_id, _path = seed_item(
        _engine, storage, due_at=now + timedelta(hours=1)
    )
    _tender, _doc, _run, expired_id, _path = seed_item(
        _engine,
        storage,
        status="processing",
        attempts=1,
        due_at=now - timedelta(seconds=2),
    )

    first = claim_next_item(session_factory, "worker-a", settings, storage, now=now)
    assert first is not None and first.item_id == expired_id
    assert first.fencing_token == 2
    assert first.item_id != future_id
    assert claim_next_item(session_factory, "worker-idle", settings, storage, now=now) is None

    reclaimed_at = first.lease_expires_at
    second = claim_next_item(session_factory, "worker-b", settings, storage, now=reclaimed_at)
    assert second is not None and second.item_id == expired_id
    assert second.fencing_token == 3


def test_postgres_invalid_document_validation_does_not_block_sibling(pg_context):
    engine, session_factory, storage, settings = pg_context
    tender_id, first_document_id, run_id, first_item_id, first_path = seed_item(
        engine, storage, status="processing", attempts=1
    )
    valid_pdf = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\nstartxref\n0\n%%EOF\n"
    first_path.write_bytes(valid_pdf)
    second_path = storage.root / f"{uuid4().hex}.pdf"
    second_path.write_bytes(valid_pdf)
    with Session(engine) as session:
        first_document = session.get(TenderDocument, first_document_id)
        run = session.get(DocumentProcessingRun, run_id)
        assert first_document is not None and run is not None
        first_document.size_bytes = len(valid_pdf)
        first_document.sha256 = hashlib.sha256(valid_pdf).hexdigest()
        first_document.extension = ".pdf"
        run.requested_document_count = 2
        sibling = TenderDocument(
            tender_id=tender_id,
            original_filename="sibling.pdf",
            storage_key=second_path.name,
            extension=".pdf",
            content_type="application/pdf",
            size_bytes=len(valid_pdf),
            sha256=hashlib.sha256(valid_pdf).hexdigest(),
            category=DocumentCategory.MAIN_TENDER,
        )
        sibling_item = DocumentProcessingRunItem(
            run=run,
            document=sibling,
            status="pending",
            attempt_count=0,
            max_attempts=3,
            fencing_token=0,
            next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        session.add(sibling_item)
        session.commit()
        sibling_document_id = sibling.id

    # Corrupt the first source after its metadata was recorded. Its permanent
    # validation failure must not prevent the same claim cycle from reaching
    # the valid sibling.
    first_path.write_bytes(b"%PDF-1.7\ncorrupted source\n%%EOF\n")
    stale_claim = ClaimedProcessingItem(
        item_id=first_item_id,
        run_id=run_id,
        document_id=first_document_id,
        tender_id=tender_id,
        worker_id="stale-worker",
        fencing_token=1,
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=2),
        source_path=first_path,
    )
    claim = claim_next_item(session_factory, "worker-isolation", settings, storage)
    assert claim is not None and claim.document_id == sibling_document_id
    with Session(engine) as session:
        invalid = session.get(DocumentProcessingRunItem, first_item_id)
        assert invalid is not None
        assert invalid.status == "failed"
        assert invalid.failure_class == "permanent"
        assert invalid.outcome_code == "source_size_mismatch"
    assert not persist_result(
        session_factory,
        stale_claim,
        [ExtractionUnitData("text", {}, content_text="stale result")],
    )
    storage.cleanup_source_stage(claim.source_path)


def test_postgres_concurrent_workers_never_claim_same_item(pg_context):
    engine, session_factory, storage, settings = pg_context
    _tender, _document, _run, item_id, _path = seed_item(engine, storage)
    barrier = threading.Barrier(2)

    def try_claim(worker_id: str):
        barrier.wait(timeout=5)
        return claim_next_item(session_factory, worker_id, settings, storage)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(try_claim, ["worker-a", "worker-b"]))

    successful = [claim for claim in claims if claim is not None]
    assert len(successful) == 1
    assert successful[0].item_id == item_id


def test_postgres_advisory_slots_enforce_two_process_limit(pg_context):
    engine, _session_factory, _storage, _settings = pg_context
    first_slot, first_connection = acquire_worker_slot(engine, 2)
    second_slot, second_connection = acquire_worker_slot(engine, 2)
    try:
        assert {first_slot, second_slot} == {0, 1}
        with pytest.raises(WorkerCapacityError, match="Maximum processing worker count"):
            acquire_worker_slot(engine, 2)
    finally:
        release_worker_slot(first_connection, first_slot)
        release_worker_slot(second_connection, second_slot)


def test_postgres_lease_validation_fencing_and_idempotent_persistence(pg_context):
    engine, session_factory, storage, settings = pg_context
    _tender, _document, _run, item_id, _path = seed_item(engine, storage)
    now = datetime.now(UTC)
    old_claim = claim_next_item(session_factory, "worker-old", settings, storage, now=now)
    assert old_claim is not None and old_claim.item_id == item_id

    assert renew_lease(session_factory, old_claim, 120, now=now + timedelta(seconds=10))
    assert not renew_lease(
        session_factory,
        ClaimedProcessingItem(**{**old_claim.__dict__, "worker_id": "worker-wrong"}),
        120,
        now=now + timedelta(seconds=20),
    )
    assert not renew_lease(
        session_factory,
        old_claim,
        120,
        now=old_claim.lease_expires_at + timedelta(seconds=10),
    )
    assert not renew_lease(
        session_factory,
        ClaimedProcessingItem(**{**old_claim.__dict__, "fencing_token": old_claim.fencing_token + 1}),
        120,
        now=now + timedelta(seconds=20),
    )

    current = claim_next_item(
        session_factory,
        "worker-new",
        settings,
        storage,
        now=old_claim.lease_expires_at + timedelta(seconds=11),
    )
    assert current is not None and current.fencing_token == old_claim.fencing_token + 1
    assert not persist_result(session_factory, old_claim, [])
    units = [ExtractionUnitData("text", {"page": 1}, content_text="synthetic")]
    assert persist_result(session_factory, current, units)
    assert persist_result(session_factory, current, units) is False
    with Session(engine) as session:
        stored = session.get(DocumentProcessingRunItem, item_id)
        assert stored is not None and stored.status == "completed"
        assert session.query(DocumentExtractionUnit).filter_by(run_item_id=item_id).count() == 1
        run = session.get(DocumentProcessingRun, stored.run_id)
        assert run is not None and run.status == "completed" and run.completed_at is not None


def test_postgres_retry_scheduling_clears_lease_and_permanent_failure_is_terminal(pg_context):
    engine, session_factory, storage, settings = pg_context
    _tender, _document, _run, retry_item_id, _path = seed_item(engine, storage)
    now = datetime.now(UTC)
    retry_claim = claim_next_item(session_factory, "worker-retry", settings, storage, now=now)
    assert retry_claim is not None
    retry = record_failure(
        session_factory,
        retry_claim,
        ProcessingFailure("retryable", "temporary_io", "The source could not be read temporarily."),
        settings,
        now=now + timedelta(seconds=1),
        random_value=lambda: 0.5,
    )
    assert retry
    with Session(engine) as session:
        item = session.get(DocumentProcessingRunItem, retry_item_id)
        assert item is not None and item.status == "pending"
        assert item.attempt_count == 1 and item.fencing_token == 1
        assert item.worker_id is None and item.lease_expires_at is None and item.heartbeat_at is None
        assert item.next_attempt_at == now + timedelta(seconds=3.5)
        run = session.get(DocumentProcessingRun, item.run_id)
        assert run is not None and run.status == "pending" and run.completed_at is None

    _tender, _document, _run, permanent_item_id, _path = seed_item(engine, storage)
    permanent_claim = claim_next_item(session_factory, "worker-permanent", settings, storage)
    assert permanent_claim is not None
    assert record_failure(
        session_factory,
        permanent_claim,
        ProcessingFailure("permanent", "unsupported_format", "This file format is unsupported."),
        settings,
    )
    with Session(engine) as session:
        item = session.get(DocumentProcessingRunItem, permanent_item_id)
        assert item is not None and item.status == "failed"
        assert item.failure_class == "permanent"
        assert item.worker_id is None and item.lease_expires_at is None
        run = session.get(DocumentProcessingRun, item.run_id)
        assert run is not None and run.status == "failed" and run.completed_at is not None


def test_postgres_lost_commit_ack_is_confirmed_without_duplicate_units(pg_context):
    engine, session_factory, storage, settings = pg_context
    _tender, _document, _run, item_id, _path = seed_item(engine, storage)
    claim = claim_next_item(session_factory, "worker-a", settings, storage)
    assert claim is not None
    from app.services.processing_handler import ExtractionUnitData

    def raise_once(_session):
        raise SQLAlchemyError("simulated lost acknowledgement")

    event.listen(Session, "after_commit", raise_once)
    try:
        assert persist_result(
            session_factory,
            claim,
            [ExtractionUnitData("text", {"page": 1}, content_text="synthetic")],
        )
    finally:
        # Ensure the global SQLAlchemy event hook cannot affect another test.
        try:
            event.remove(Session, "after_commit", raise_once)
        except Exception:
            pass
    # A second attempt with the same (now completed) claim must be rejected;
    # the ambiguous commit acknowledgement must not duplicate its units.
    assert not persist_result(
        session_factory,
        claim,
        [ExtractionUnitData("text", {"page": 1}, content_text="synthetic")],
    )
    with Session(engine) as session:
        assert session.get(DocumentProcessingRunItem, item_id).status == "completed"
        assert session.query(DocumentExtractionUnit).filter_by(run_item_id=item_id).count() == 1


def test_postgres_deleted_or_missing_source_cannot_be_claimed_or_persisted(pg_context):
    engine, session_factory, storage, settings = pg_context
    _tender, document_id, _run, item_id, _path = seed_item(engine, storage)
    claim = claim_next_item(session_factory, "worker-a", settings, storage)
    assert claim is not None
    with Session(engine) as session:
        session.delete(session.get(TenderDocument, document_id))
        session.commit()
    assert claim_next_item(session_factory, "worker-after-delete", settings, storage) is None
    assert not renew_lease(session_factory, claim, 120)
    assert not persist_result(
        session_factory,
        claim,
        [ExtractionUnitData("text", {}, content_text="must not survive")],
    )
    with Session(engine) as session:
        item = session.get(DocumentProcessingRunItem, item_id)
        assert item is not None and item.document_id is None
        assert item.status == "failed" and item.outcome_code == "source_deleted"
        assert item.fencing_token == claim.fencing_token + 1
        assert item.worker_id is None and item.lease_expires_at is None
        assert session.query(DocumentExtractionUnit).filter_by(run_item_id=item_id).count() == 0


def test_postgres_source_key_share_lock_serializes_document_delete(pg_context):
    engine, _session_factory, storage, _settings = pg_context
    _tender, document_id, _run, _item, _path = seed_item(engine, storage)
    delete_started = threading.Event()
    delete_finished = threading.Event()

    def delete_source():
        delete_started.set()
        with Session(engine) as session:
            session.delete(session.get(TenderDocument, document_id))
            session.commit()
        delete_finished.set()

    delete_thread = threading.Thread(target=delete_source)
    with Session(engine) as locker:
        with locker.begin():
            locked = locker.scalar(
                select(TenderDocument)
                .where(TenderDocument.id == document_id)
                .with_for_update(read=True, key_share=True)
            )
            assert locked is not None
            delete_thread.start()
            assert delete_started.wait(timeout=2)
            time.sleep(0.15)
            assert not delete_finished.is_set()
    delete_thread.join(timeout=5)
    assert delete_finished.is_set()


def test_postgres_exhausted_expired_item_fails_without_reclaim(pg_context):
    engine, session_factory, storage, settings = pg_context
    _tender, _document, _run, item_id, _path = seed_item(
        engine, storage, status="processing", attempts=3, max_attempts=3
    )
    assert claim_next_item(session_factory, "worker-new", settings, storage) is None
    with Session(engine) as session:
        item = session.get(DocumentProcessingRunItem, item_id)
        assert item is not None
        assert item.status == "failed"
        assert item.outcome_code == "attempt_limit_exceeded"
        assert item.worker_id is None and item.lease_expires_at is None


def test_postgres_source_deletion_invalidates_claim_and_removes_result(pg_context):
    engine, session_factory, storage, settings = pg_context
    _tender_id, document_id, _run_id, item_id, _path = seed_item(engine, storage)
    claim = claim_next_item(session_factory, "worker-before-delete", settings, storage)
    assert claim is not None
    assert persist_result(
        session_factory,
        claim,
        [ExtractionUnitData("text", {"page": 1}, content_text="derived")],
    )

    with Session(engine) as session:
        delete_source_document(session, document_id, storage)

    assert not persist_result(
        session_factory,
        claim,
        [ExtractionUnitData("text", {}, content_text="stale")],
    )
    with Session(engine) as session:
        item = session.get(DocumentProcessingRunItem, item_id)
        assert item is not None
        assert item.document_id is None
        assert item.status == "failed" and item.outcome_code == "source_deleted"
        assert item.safe_message is None and item.worker_id is None
        assert item.fencing_token == claim.fencing_token + 1
        assert session.query(DocumentExtractionUnit).filter_by(run_item_id=item_id).count() == 0


def test_postgres_persistence_and_heartbeat_racing_with_delete_leave_no_result(pg_context):
    engine, session_factory, storage, settings = pg_context
    _tender_id, document_id, _run_id, item_id, _path = seed_item(engine, storage)
    claim = claim_next_item(session_factory, "worker-delete-race", settings, storage)
    assert claim is not None
    # Three racer threads plus the test thread wait at the barrier.
    barrier = threading.Barrier(4)

    def await_start_barrier(racer_name: str) -> None:
        try:
            barrier.wait(timeout=5)
        except threading.BrokenBarrierError as exc:
            raise RuntimeError(f"{racer_name} did not reach the race start barrier") from exc

    def persist_racer():
        await_start_barrier("persistence racer")
        return persist_result(
            session_factory,
            claim,
            [ExtractionUnitData("text", {}, content_text="race result")],
        )

    def heartbeat_racer():
        await_start_barrier("heartbeat racer")
        return renew_lease(session_factory, claim, 120)

    def delete_racer():
        await_start_barrier("deletion racer")
        with Session(engine) as session:
            delete_source_document(session, document_id, storage)

    with ThreadPoolExecutor(max_workers=3) as pool:
        persisted = pool.submit(persist_racer)
        heartbeat = pool.submit(heartbeat_racer)
        deletion = pool.submit(delete_racer)
        barrier.wait(timeout=5)
        # Resolve each future separately so a worker-thread exception is
        # attributed to the persistence, heartbeat, or deletion racer.
        for name, future in (
            ("persistence", persisted),
            ("heartbeat", heartbeat),
            ("deletion", deletion),
        ):
            try:
                future.result(timeout=10)
            except Exception as exc:
                pytest.fail(f"{name} racer raised {type(exc).__name__}: {exc}", pytrace=True)

    with Session(engine) as session:
        item = session.get(DocumentProcessingRunItem, item_id)
        assert item is not None and item.status == "failed"
        assert item.outcome_code == "source_deleted" and item.fencing_token > claim.fencing_token
        assert item.document_id is None and item.safe_message is None
        assert session.query(DocumentExtractionUnit).filter_by(run_item_id=item_id).count() == 0


def test_postgres_tender_deletion_cleans_multiple_processing_sources(pg_context):
    engine, session_factory, storage, settings = pg_context
    first = seed_item(engine, storage)
    second = seed_item(engine, storage)
    claims = [
        claim_next_item(session_factory, "worker-one", settings, storage),
        claim_next_item(session_factory, "worker-two", settings, storage),
    ]
    assert all(claim is not None for claim in claims)
    tender_ids = {first[0], second[0]}
    for tender_id in tender_ids:
        with Session(engine) as session:
            delete_tender_sources(
                session, tender_id, storage_factory=lambda: storage
            )

    assert all(not persist_result(session_factory, claim, []) for claim in claims)
    with Session(engine) as session:
        assert session.query(Tender).filter(Tender.id.in_(tender_ids)).count() == 0
        assert session.query(DocumentExtractionUnit).count() == 0


def test_postgres_tender_delete_serializes_with_upload(pg_context, monkeypatch):
    engine, session_factory, storage, _settings = pg_context
    with Session(engine) as session:
        tender = Tender(name=f"Upload race {uuid4()}")
        session.add(tender)
        session.commit()
        tender_id = tender.id

    promoting = threading.Event()
    allow_promote = threading.Event()
    real_promote = storage.promote

    def blocked_promote(path, extension):
        promoting.set()
        assert allow_promote.wait(timeout=5)
        return real_promote(path, extension)

    monkeypatch.setattr(storage, "promote", blocked_promote)
    upload_result = []
    deletion_result = []

    def upload():
        with Session(engine) as session:
            upload_result.extend(
                upload_documents(
                    session,
                    tender_id,
                    [UploadFile(filename="race.pdf", file=BytesIO(b"%PDF-1.7 race"))],
                    [DocumentCategory.MAIN_TENDER.value],
                    storage,
                    1024,
                    2048,
                )
            )

    def delete():
        with Session(engine) as session:
            delete_tender_sources(session, tender_id, storage_factory=lambda: DocumentStorage(storage.root))
        deletion_result.append(True)

    uploader = threading.Thread(target=upload)
    deleter = threading.Thread(target=delete)
    uploader.start()
    assert promoting.wait(timeout=5)
    deleter.start()
    time.sleep(0.15)
    assert not deletion_result
    allow_promote.set()
    uploader.join(timeout=10)
    deleter.join(timeout=10)
    assert not uploader.is_alive() and not deleter.is_alive()
    assert upload_result and deletion_result
    with Session(engine) as session:
        assert session.get(Tender, tender_id) is None
        assert session.query(TenderDocument).filter_by(tender_id=tender_id).count() == 0
    assert list(storage.root.glob("*.pdf")) == []


def test_postgres_concurrent_document_deletion_is_serialized(pg_context):
    engine, _factory, storage, _settings = pg_context
    _tender_id, document_id, _run_id, _item_id, _path = seed_item(engine, storage)
    start = threading.Barrier(3)
    outcomes = []

    def delete():
        start.wait(timeout=5)
        try:
            with Session(engine) as session:
                delete_source_document(session, document_id, DocumentStorage(storage.root))
            outcomes.append("deleted")
        except LookupError:
            outcomes.append("already_deleted")

    threads = [threading.Thread(target=delete) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["already_deleted", "deleted"]
    assert not _path.exists()


def test_postgres_claim_stages_source_before_delete_can_quarantine(pg_context, monkeypatch):
    engine, session_factory, storage, settings = pg_context
    _tender_id, document_id, _run_id, _item_id, original = seed_item(engine, storage)
    staged = threading.Event()
    allow_copy = threading.Event()
    real_stage = storage.stage_source

    def blocked_stage(key):
        staged.set()
        assert allow_copy.wait(timeout=5)
        return real_stage(key)

    monkeypatch.setattr(storage, "stage_source", blocked_stage)
    claims = []
    deletion_done = threading.Event()

    def claim():
        claims.append(claim_next_item(session_factory, "stage-racer", settings, storage))

    def delete():
        with Session(engine) as session:
            delete_source_document(session, document_id, DocumentStorage(storage.root))
        deletion_done.set()

    claimant = threading.Thread(target=claim)
    claimant.start()
    assert staged.wait(timeout=5)
    deleter = threading.Thread(target=delete)
    deleter.start()
    time.sleep(0.15)
    assert not deletion_done.is_set()
    allow_copy.set()
    claimant.join(timeout=10)
    deleter.join(timeout=10)
    assert not claimant.is_alive() and not deleter.is_alive()
    assert claims[0] is not None
    assert claims[0].source_path.exists()
    assert not original.exists()
    assert not persist_result(session_factory, claims[0], [])
    storage.cleanup_source_stage(claims[0].source_path)


def test_postgres_recovery_restores_partial_quarantine_and_is_idempotent(pg_context):
    engine, _factory, storage, _settings = pg_context
    first = seed_item(engine, storage)
    second = seed_item(engine, storage)
    with Session(engine) as session:
        documents = list(
            session.scalars(
                select(TenderDocument).where(TenderDocument.tender_id.in_([first[0], second[0]]))
            ).all()
        )
    docs = [(doc.id, doc.tender_id, doc.storage_key) for doc in documents]
    manifest, _moved = storage.quarantine_documents(docs)
    assert manifest is not None
    payload = __import__("json").loads(manifest.read_text(encoding="utf-8"))
    record = payload["files"][0]
    # quarantine_documents performs the complete move. Recreate a crash after
    # only the later record was moved by restoring this record's held file to
    # its original location; recovery must then restore it and the other held
    # source from the still-durable manifest.
    os.replace(storage.quarantine / record["held"], storage.resolve_key(record["source"]))
    storage._fsync_dir(storage.root)
    storage._fsync_dir(storage.quarantine)
    storage._release_journal_lock()

    recovered = DocumentStorage(storage.root)
    recovered.recover_pending_deletions(lambda: Session(engine))
    recovered.recover_pending_deletions(lambda: Session(engine))
    assert all(storage.resolve_key(doc.storage_key).exists() for doc in documents)
    assert list(storage.quarantine.glob("*.held")) == []
    assert list(storage.quarantine.glob("delete-*.json")) == []


def test_postgres_recovery_purges_after_commit_before_purge(pg_context, monkeypatch):
    engine, _factory, storage, _settings = pg_context
    _tender_id, document_id, _run_id, _item_id, original = seed_item(engine, storage)
    with Session(engine) as session:
        document = session.get(TenderDocument, document_id)
        assert document is not None
        manifest, _moved = storage.quarantine_documents(
            [(document.id, document.tender_id, document.storage_key)]
        )
        assert manifest is not None
        session.delete(document)
        session.commit()
    storage._release_journal_lock()

    recovered = DocumentStorage(storage.root)
    recovered.recover_pending_deletions(lambda: Session(engine))
    recovered.recover_pending_deletions(lambda: Session(engine))
    assert not original.exists()
    assert list(storage.quarantine.glob("*.held")) == []
    assert list(storage.quarantine.glob("delete-*.json")) == []


def test_postgres_database_rollback_after_quarantine_restores_source(pg_context):
    engine, _factory, storage, _settings = pg_context
    _tender_id, document_id, _run_id, _item_id, original = seed_item(engine, storage)

    def fail_commit(_session):
        raise SQLAlchemyError("simulated pre-commit failure")

    event.listen(Session, "before_commit", fail_commit)
    try:
        with Session(engine) as session:
            with pytest.raises(SQLAlchemyError):
                delete_source_document(session, document_id, storage)
    finally:
        event.remove(Session, "before_commit", fail_commit)

    assert original.exists()
    assert list(storage.quarantine.glob("*.held")) == []
    assert list(storage.quarantine.glob("delete-*.json")) == []
    with Session(engine) as session:
        assert session.get(TenderDocument, document_id) is not None
