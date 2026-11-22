from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tender import Tender
from app.models.tender_document import DocumentCategory, TenderDocument
from app.services.document_storage import DocumentStorage
from app.services.source_deletion import delete_source_document

logger = logging.getLogger(__name__)
ALLOWED = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".png", ".jpg", ".jpeg"}


class TenderNotFoundError(LookupError):
    """Upload target tender disappeared before the upload transaction acquired it."""


def canonical_type(extension: str) -> str:
    return {".doc": "application/msword", ".xls": "application/vnd.ms-excel", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".csv": "text/csv", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(extension, mimetypes.types_map.get(extension, "application/octet-stream"))


def validate_upload_name(name: str | None) -> tuple[str, str]:
    if not name or name in {".", ".."} or "/" in name or "\\" in name or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError("Invalid filename")
    extension = Path(name).suffix.lower()
    if extension not in ALLOWED:
        raise TypeError("Unsupported file type")
    return name, extension


def validate_signature(extension: str, prefix: bytes) -> None:
    valid = {
        ".pdf": prefix.startswith(b"%PDF-"),
        ".png": prefix.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": prefix.startswith(b"\xff\xd8\xff"),
        ".jpeg": prefix.startswith(b"\xff\xd8\xff"),
        ".doc": prefix.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
        ".xls": prefix.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
        ".docx": prefix.startswith(b"PK\x03\x04"),
        ".xlsx": prefix.startswith(b"PK\x03\x04"),
        ".csv": b"\x00" not in prefix and all(char.isprintable() or char in "\r\n\t" for char in prefix.decode("utf-8-sig", errors="ignore")),
    }.get(extension, False)
    if not valid:
        raise TypeError("File content does not match its extension")


def get_document(session: Session, tender_id: UUID, document_id: UUID) -> TenderDocument | None:
    return session.scalar(select(TenderDocument).where(TenderDocument.id == document_id, TenderDocument.tender_id == tender_id))


def list_documents(session: Session, tender_id: UUID, *, limit: int, offset: int, category: DocumentCategory | None) -> tuple[list[TenderDocument], int]:
    filters = [TenderDocument.tender_id == tender_id]
    if category is not None:
        filters.append(TenderDocument.category == category)
    total = session.scalar(select(func.count()).select_from(TenderDocument).where(*filters)) or 0
    items = list(session.scalars(select(TenderDocument).where(*filters).order_by(TenderDocument.created_at.desc(), TenderDocument.id.desc()).limit(limit).offset(offset)).all())
    return items, total


def upload_documents(session: Session, tender_id: UUID, uploads: list[UploadFile], categories: list[str], storage: DocumentStorage, max_file: int, max_batch: int) -> list[TenderDocument]:
    if len(uploads) != len(categories):
        raise ValueError("File and category counts must match")
    parsed: list[DocumentCategory] = []
    for category in categories:
        try:
            parsed.append(DocumentCategory(category))
        except ValueError as exc:
            raise LookupError("Invalid category") from exc
    staged: list[Path] = []
    promoted: list[str] = []
    records: list[TenderDocument] = []
    total_bytes = 0
    try:
        # Shared parent-first lock protocol with tender deletion. It is held
        # through promotion and metadata commit, closing the deletion snapshot.
        tender = session.scalar(select(Tender).where(Tender.id == tender_id).with_for_update())
        if tender is None:
            raise TenderNotFoundError
        for upload, category in zip(uploads, parsed):
            original_name, extension = validate_upload_name(upload.filename)
            path, digest, size, prefix = storage.stage(upload, max_file)
            staged.append(path)
            total_bytes += size
            if total_bytes > max_batch:
                raise OverflowError("batch")
            validate_signature(extension, prefix)
            key = storage.promote(path, extension)
            staged.remove(path)
            promoted.append(key)
            records.append(TenderDocument(tender_id=tender_id, original_filename=original_name, storage_key=key, extension=extension, content_type=canonical_type(extension), size_bytes=size, sha256=digest, category=category))
        session.add_all(records)
        session.commit()
        return records
    except BaseException:
        session.rollback()
        storage.cleanup_staging(staged)
        for key in promoted:
            try:
                storage.remove(key)
            except OSError:
                logger.exception("Failed to remove an unregistered staged document")
        raise


def delete_document(session: Session, document: TenderDocument, storage: DocumentStorage) -> None:
    delete_source_document(session, document.id, storage)
