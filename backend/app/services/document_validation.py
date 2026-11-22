"""Deterministic validation and classification of stored processing sources.

This module checks storage integrity and file containers only. It deliberately
does not extract document content or execute anything embedded in a file.
"""

from __future__ import annotations

import codecs
import hashlib
import re
import stat
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.services.document_storage import DocumentStorage, StorageError

CHUNK_SIZE = 1024 * 1024
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".csv", ".png", ".jpg", ".jpeg"}
LEGACY_EXTENSIONS = {".doc", ".xls"}
ALLOWED_EXTENSIONS = SUPPORTED_EXTENSIONS | LEGACY_EXTENSIONS
OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"
PDF_HEADER = re.compile(rb"%PDF-(?:1\.[0-7]|2\.0)")


@dataclass(frozen=True)
class ValidationResult:
    """A safe validation result; failures contain only an approved code."""

    path: Path | None
    detected_format: str | None
    outcome_code: str | None

    @property
    def valid(self) -> bool:
        return self.outcome_code is None and self.path is not None


def _failure(code: str) -> ValidationResult:
    return ValidationResult(path=None, detected_format=None, outcome_code=code)


def _metadata(document, requested_tender_id: UUID) -> tuple[str, int, str] | ValidationResult:
    key = getattr(document, "storage_key", None)
    extension = getattr(document, "extension", None)
    size = getattr(document, "size_bytes", None)
    digest = getattr(document, "sha256", None)
    if (
        getattr(document, "tender_id", None) != requested_tender_id
        or not isinstance(key, str)
        or not isinstance(extension, str)
        or not extension.startswith(".")
        or extension != extension.lower()
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None
    ):
        return _failure("invalid_document_metadata")
    if not key.strip():
        return _failure("unsafe_storage_key")
    if extension not in ALLOWED_EXTENSIONS:
        return _failure("unsupported_format")
    return extension, size, digest.lower()


def _within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def validate_document_source(
    document,
    requested_tender_id: UUID,
    storage: DocumentStorage,
    maximum_size_bytes: int,
    csv_fallback_encodings: str = "cp1252",
) -> ValidationResult:
    """Validate metadata and the original private source for one tender item."""
    metadata = _metadata(document, requested_tender_id)
    if isinstance(metadata, ValidationResult):
        return metadata
    extension, expected_size, expected_digest = metadata
    key = document.storage_key

    try:
        # resolve_key rejects non-leaf keys. Resolve strictly again so a
        # symlink cannot escape the configured private root.
        candidate = storage.resolve_key(key)
        root = storage.root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except (StorageError, ValueError, RuntimeError):
        return _failure("unsafe_storage_key")
    except FileNotFoundError:
        return _failure("source_unavailable")
    except OSError:
        return _failure("source_unavailable")

    if not _within_root(resolved, root):
        return _failure("unsafe_storage_key")
    return _validate_file(
        resolved, extension, expected_size, expected_digest, maximum_size_bytes,
        csv_fallback_encodings,
    )


def validate_staged_source(
    path: Path,
    document,
    storage: DocumentStorage,
    maximum_size_bytes: int,
    csv_fallback_encodings: str = "cp1252",
) -> ValidationResult:
    """Revalidate the exact private staged copy that the handler will receive."""
    metadata = _metadata(document, document.tender_id)
    if isinstance(metadata, ValidationResult):
        return metadata
    extension, expected_size, expected_digest = metadata
    try:
        root = storage.root.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        return _failure("source_unavailable")
    except (OSError, RuntimeError):
        return _failure("source_unavailable")
    if not _within_root(resolved, root):
        return _failure("unsafe_storage_key")
    return _validate_file(
        resolved, extension, expected_size, expected_digest, maximum_size_bytes,
        csv_fallback_encodings,
    )


def _validate_file(
    path: Path,
    extension: str,
    expected_size: int,
    expected_digest: str,
    maximum_size_bytes: int,
    csv_fallback_encodings: str,
) -> ValidationResult:
    try:
        file_stat = path.stat()
        if not stat.S_ISREG(file_stat.st_mode):
            return _failure("source_unavailable")
        actual_size = file_stat.st_size
        if actual_size == 0:
            return _failure("empty_file")
        if actual_size > maximum_size_bytes:
            return _failure("resource_limit_exceeded")
        if actual_size != expected_size:
            return _failure("source_size_mismatch")

        digest = hashlib.sha256()
        prefix = bytearray()
        bytes_read = 0
        with path.open("rb") as source:
            while chunk := source.read(min(CHUNK_SIZE, maximum_size_bytes + 1 - bytes_read)):
                bytes_read += len(chunk)
                if bytes_read > maximum_size_bytes:
                    return _failure("resource_limit_exceeded")
                digest.update(chunk)
                if len(prefix) < 4096:
                    prefix.extend(chunk[: 4096 - len(prefix)])
        if bytes_read != actual_size:
            return _failure("source_size_mismatch")
        if digest.hexdigest() != expected_digest:
            return _failure("source_hash_mismatch")
        detected, structure_error = _detect_format(path, bytes(prefix), actual_size)
    except (PermissionError, FileNotFoundError, IsADirectoryError, OSError):
        return _failure("source_unavailable")
    except (zipfile.BadZipFile, EOFError, ValueError, OverflowError):
        return _failure("malformed_file")

    if structure_error:
        return _failure("malformed_file")
    if detected is None:
        # CSV has no fixed signature and is classified by bounded text checks.
        if extension == ".csv":
            if not _valid_csv_text(path, maximum_size_bytes, csv_fallback_encodings):
                return _failure("malformed_file")
            detected = ".csv"
        else:
            return _failure("malformed_file")

    if extension in LEGACY_EXTENSIONS and detected == "ole":
        return ValidationResult(path, detected, "unsupported_format")
    if detected == "ole":
        return _failure("extension_signature_mismatch")
    if extension == ".jpg" and detected == ".jpeg":
        detected = ".jpg"
    if detected != extension:
        return _failure("extension_signature_mismatch")
    if extension in LEGACY_EXTENSIONS:
        return _failure("malformed_file")
    return ValidationResult(path=path, detected_format=detected, outcome_code=None)


def _detect_format(path: Path, prefix: bytes, size: int) -> tuple[str | None, bool]:
    if PDF_HEADER.search(prefix[:1024]):
        with path.open("rb") as source:
            source.seek(max(0, size - 2048))
            tail = source.read(2048)
        return ".pdf", b"startxref" not in tail or b"%%EOF" not in tail

    if prefix.startswith(PNG_SIGNATURE):
        with path.open("rb") as source:
            header = source.read(33)
            source.seek(max(0, size - 12))
            trailer = source.read(12)
        valid_header = (
            size >= 45
            and len(header) == 33
            and int.from_bytes(header[8:12], "big") == 13
            and header[12:16] == b"IHDR"
        )
        valid_trailer = len(trailer) == 12 and trailer[:8] == b"\x00\x00\x00\x00IEND"
        return ".png", not (valid_header and valid_trailer)

    if prefix.startswith(JPEG_SIGNATURE):
        return ".jpeg", not _valid_jpeg_structure(path, size)

    if prefix.startswith(OLE_SIGNATURE):
        return "ole", False

    if prefix.startswith(b"PK"):
        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if len(members) > 10000:
                    return None, True
                member_counts: dict[str, int] = {}
                for member in members:
                    member_counts[member.filename] = member_counts.get(member.filename, 0) + 1
                has_docx = all(
                    member_counts.get(name) == 1
                    for name in ("[Content_Types].xml", "word/document.xml")
                )
                has_xlsx = all(
                    member_counts.get(name) == 1
                    for name in ("[Content_Types].xml", "xl/workbook.xml")
                )
                if has_docx == has_xlsx:
                    return None, True
                detected = ".docx" if has_docx else ".xlsx"
                required = (
                    ("[Content_Types].xml", "word/document.xml")
                    if has_docx
                    else ("[Content_Types].xml", "xl/workbook.xml")
                )
                by_name = {member.filename: member for member in members}
                for name in required:
                    member = by_name[name]
                    if member.file_size == 0:
                        return detected, True
                    # Reading to EOF through ZipExtFile verifies decompression
                    # and CRC without extracting the member or retaining it.
                    with archive.open(member) as source:
                        bytes_read = 0
                        while chunk := source.read(CHUNK_SIZE):
                            bytes_read += len(chunk)
                        if bytes_read == 0:
                            return detected, True
                return detected, False
        except (zipfile.BadZipFile, OSError, KeyError, RuntimeError, EOFError, zlib.error, ValueError):
            return None, True

    return None, False


def _valid_csv_text(path: Path, maximum_size_bytes: int, fallback_encodings: str) -> bool:
    """Boundedly validate CSV text using the same strict encoding order as extraction."""
    encoding_names = ["utf-8-sig"]
    try:
        with path.open("rb") as source:
            prefix = source.read(4)
    except OSError:
        return False
    has_utf8_bom = prefix.startswith(codecs.BOM_UTF8)
    has_unsupported_bom = prefix.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE, codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE))
    if has_unsupported_bom and not has_utf8_bom:
        return False
    if not has_utf8_bom:
        for candidate in fallback_encodings.split(","):
            candidate = candidate.strip()
            if candidate:
                encoding = codecs.lookup(candidate).name
                if encoding not in encoding_names:
                    encoding_names.append(encoding)
    for encoding in encoding_names:
        decoder = codecs.getincrementaldecoder(encoding)("strict")
        bytes_read = 0
        try:
            with path.open("rb") as source:
                while chunk := source.read(min(CHUNK_SIZE, maximum_size_bytes + 1 - bytes_read)):
                    bytes_read += len(chunk)
                    if bytes_read > maximum_size_bytes:
                        return False
                    if not _text_has_safe_csv_characters(decoder.decode(chunk, final=False)):
                        break
                else:
                    if _text_has_safe_csv_characters(decoder.decode(b"", final=True)):
                        return True
        except UnicodeDecodeError:
            continue
        except OSError:
            return False
    return False


def _valid_jpeg_structure(path: Path, size: int) -> bool:
    """Check marker lengths through SOS and require EOI, without decoding pixels."""
    if size < 12:
        return False
    saw_frame = False
    saw_scan = False
    try:
        with path.open("rb") as source:
            if source.read(2) != b"\xff\xd8":
                return False
            while source.tell() < size:
                byte = source.read(1)
                if not byte:
                    return False
                if byte != b"\xff":
                    return False
                marker_byte = source.read(1)
                while marker_byte == b"\xff":
                    marker_byte = source.read(1)
                if not marker_byte:
                    return False
                marker = marker_byte[0]
                if marker == 0xD9:
                    break
                if marker in {0xD8, 0x01, *range(0xD0, 0xD8)}:
                    continue
                segment_length_bytes = source.read(2)
                if len(segment_length_bytes) != 2:
                    return False
                segment_length = int.from_bytes(segment_length_bytes, "big")
                if segment_length < 2 or source.tell() + segment_length - 2 > size:
                    return False
                if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                    if segment_length < 8:
                        return False
                    saw_frame = True
                if marker == 0xDA:
                    saw_scan = True
                    break
                source.seek(segment_length - 2, 1)
            source.seek(size - 2)
            return saw_frame and saw_scan and source.read(2) == b"\xff\xd9"
    except OSError:
        return False


def _text_has_safe_csv_characters(value: str) -> bool:
    return all(
        char in "\r\n\t" or not (unicodedata.category(char) == "Cc")
        for char in value
    )


def _company_metadata(version, company_document, requested_company_id: UUID):
    """Validate immutable company-version metadata and its parent scope."""
    key = getattr(version, "storage_key", None)
    extension = getattr(version, "extension", None)
    size = getattr(version, "size_bytes", None)
    digest = getattr(version, "sha256", None)
    if (
        getattr(company_document, "company_id", None) != requested_company_id
        or not isinstance(key, str)
        or not isinstance(extension, str)
        or not extension.startswith(".")
        or extension != extension.lower()
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None
    ):
        return _failure("invalid_document_metadata")
    if not key.strip():
        return _failure("unsafe_storage_key")
    if extension not in ALLOWED_EXTENSIONS:
        return _failure("unsupported_format")
    return extension, size, digest.lower()


def validate_company_document_source(
    version,
    company_document,
    requested_company_id: UUID,
    storage: DocumentStorage,
    maximum_size_bytes: int,
    csv_fallback_encodings: str = "cp1252",
) -> ValidationResult:
    """Validate a stored immutable company-document version."""
    metadata = _company_metadata(version, company_document, requested_company_id)
    if isinstance(metadata, ValidationResult):
        return metadata
    extension, expected_size, expected_digest = metadata
    try:
        candidate = storage.resolve_key(version.storage_key)
        root = storage.root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except (StorageError, ValueError, RuntimeError):
        return _failure("unsafe_storage_key")
    except (FileNotFoundError, OSError):
        return _failure("source_unavailable")
    if not _within_root(resolved, root):
        return _failure("unsafe_storage_key")
    return _validate_file(
        resolved, extension, expected_size, expected_digest, maximum_size_bytes,
        csv_fallback_encodings,
    )


def validate_company_document_staged_source(
    path: Path,
    version,
    company_document,
    requested_company_id: UUID,
    storage: DocumentStorage,
    maximum_size_bytes: int,
    csv_fallback_encodings: str = "cp1252",
) -> ValidationResult:
    """Revalidate the staged private copy of a company-document version."""
    metadata = _company_metadata(version, company_document, requested_company_id)
    if isinstance(metadata, ValidationResult):
        return metadata
    extension, expected_size, expected_digest = metadata
    try:
        root = storage.root.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        return _failure("source_unavailable")
    except (OSError, RuntimeError):
        return _failure("source_unavailable")
    if not _within_root(resolved, root):
        return _failure("unsafe_storage_key")
    return _validate_file(
        resolved, extension, expected_size, expected_digest, maximum_size_bytes,
        csv_fallback_encodings,
    )
