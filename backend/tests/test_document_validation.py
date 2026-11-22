from __future__ import annotations

import hashlib
import os
import platform
import struct
import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from pydantic import ValidationError

from app.core.config import Settings
from app.schemas.processing import MAX_DOCUMENTS_PER_PROCESSING_RUN, ProcessingRunCreate
from app.services.document_storage import DocumentStorage
from app.services.document_validation import (
    validate_document_source,
    validate_staged_source,
)


TENDER_ID = uuid4()
MAX_SIZE = 50 * 1024 * 1024


def make_docx() -> bytes:
    return make_zip({"[Content_Types].xml": b"types", "word/document.xml": b"document"})


def make_xlsx() -> bytes:
    return make_zip({"[Content_Types].xml": b"types", "xl/workbook.xml": b"workbook"})


def make_zip(members: dict[str, bytes]) -> bytes:
    from io import BytesIO

    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return output.getvalue()


def mutate_zip_member(content: bytes, member_name: str, *, corrupt_payload: bool = False, corrupt_crc: bool = False) -> bytes:
    from io import BytesIO

    result = bytearray(content)
    with zipfile.ZipFile(BytesIO(content)) as archive:
        info = archive.getinfo(member_name)
        if corrupt_payload:
            data_offset = info.header_offset + 30 + len(info.filename.encode("utf-8")) + len(info.extra)
            result[data_offset] ^= 0xFF
        if corrupt_crc:
            name = info.filename.encode("utf-8")
            central = result.find(b"PK\x01\x02")
            while central >= 0:
                name_length = int.from_bytes(result[central + 28 : central + 30], "little")
                extra_length = int.from_bytes(result[central + 30 : central + 32], "little")
                comment_length = int.from_bytes(result[central + 32 : central + 34], "little")
                if result[central + 46 : central + 46 + name_length] == name:
                    result[central + 16] ^= 0xFF
                    break
                central = result.find(
                    b"PK\x01\x02", central + 46 + name_length + extra_length + comment_length
                )
            else:
                raise AssertionError(f"ZIP member not found: {member_name}")
    return bytes(result)


def make_zip_with_duplicate_member(duplicate_name: str) -> bytes:
    from io import BytesIO

    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("[Content_Types].xml", b"types")
        archive.writestr(duplicate_name, b"document one")
        archive.writestr(duplicate_name, b"document two")
    return output.getvalue()


def make_png() -> bytes:
    header = struct.pack(">I", 13) + b"IHDR" + bytes(13) + bytes(4)
    end = bytes(4) + b"IEND" + bytes(4)
    return b"\x89PNG\r\n\x1a\n" + header + end


def make_jpeg() -> bytes:
    frame = b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    scan = b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
    return b"\xff\xd8" + frame + scan + b"\x00\xff\xd9"


def make_document(storage: DocumentStorage, content: bytes, extension: str, **overrides):
    key = f"{uuid4().hex}{extension}"
    path = storage.root / key
    path.write_bytes(content)
    values = {
        "id": uuid4(),
        "tender_id": TENDER_ID,
        "storage_key": key,
        "extension": extension,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "original_filename": f"source{extension}",
        "content_type": "application/octet-stream",
    }
    values.update(overrides)
    return SimpleNamespace(**values), path


def validate(storage: DocumentStorage, document, *, maximum_size: int = MAX_SIZE):
    return validate_document_source(document, TENDER_ID, storage, maximum_size)


@pytest.fixture
def storage(tmp_path) -> DocumentStorage:
    return DocumentStorage(tmp_path / "private")


@pytest.mark.parametrize(
    "extension,content",
    [
        (".pdf", b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\nstartxref\n0\n%%EOF\n"),
        (".docx", make_docx()),
        (".xlsx", make_xlsx()),
        (".csv", b"name,value\nTender,42\n"),
        (".csv", b"\xef\xbb\xbfname,value\r\nTender,42\r\n"),
        (".png", make_png()),
        (".jpg", make_jpeg()),
        (".jpeg", make_jpeg()),
    ],
)
def test_valid_supported_signatures(storage, extension: str, content: bytes) -> None:
    document, path = make_document(storage, content, extension)
    result = validate(storage, document)
    assert result.valid
    assert result.path == path.resolve()


@pytest.mark.parametrize("extension", [".doc", ".xls"])
def test_valid_legacy_ole_files_are_explicitly_unsupported(storage, extension: str) -> None:
    document, _ = make_document(storage, bytes.fromhex("D0CF11E0A1B11AE1") + bytes(32), extension)
    result = validate(storage, document)
    assert result.outcome_code == "unsupported_format"


@pytest.mark.parametrize(
    "extension,content,outcome",
    [
        (".pdf", make_png(), "extension_signature_mismatch"),
        (".docx", make_zip({"unrelated.txt": b"not a document"}), "malformed_file"),
        (".xlsx", make_docx(), "extension_signature_mismatch"),
        (".csv", b"\x00\xff\x10", "malformed_file"),
        (".png", b"%PDF-1.7\nstartxref\n0\n%%EOF", "extension_signature_mismatch"),
    ],
)
def test_extension_signature_or_container_mismatch(storage, extension, content, outcome):
    document, _ = make_document(storage, content, extension)
    assert validate(storage, document).outcome_code == outcome


@pytest.mark.parametrize(
    "extension,content,outcome",
    [
        (".pdf", b"", "empty_file"),
        (".pdf", b"%PDF-1.7\ntruncated", "malformed_file"),
        (".docx", b"PK\x03\x04not-a-zip", "malformed_file"),
        (".docx", make_zip({"[Content_Types].xml": b"types"}), "malformed_file"),
        (".xlsx", make_zip({"[Content_Types].xml": b"types"}), "malformed_file"),
        (".png", b"\x89PNG\r\n\x1a\ntruncated", "malformed_file"),
        (".jpeg", b"\xff\xd8\xfftruncated", "malformed_file"),
        (".csv", b"\x81invalid-cp1252", "malformed_file"),
    ],
)
def test_empty_malformed_and_truncated_files(storage, extension, content, outcome):
    document, _ = make_document(storage, content, extension)
    assert validate(storage, document).outcome_code == outcome


@pytest.mark.parametrize(
    "extension,content,member",
    [
        (".docx", make_docx(), "word/document.xml"),
        (".xlsx", make_xlsx(), "xl/workbook.xml"),
    ],
)
def test_corrupt_required_zip_member_is_malformed(storage, extension, content, member):
    corrupted = mutate_zip_member(content, member, corrupt_payload=True)
    document, _ = make_document(storage, corrupted, extension)
    first = validate(storage, document)
    second = validate(storage, document)
    assert first.outcome_code == second.outcome_code == "malformed_file"


def test_required_zip_member_invalid_crc_is_malformed(storage):
    corrupted = mutate_zip_member(make_docx(), "word/document.xml", corrupt_crc=True)
    document, _ = make_document(storage, corrupted, ".docx")
    assert validate(storage, document).outcome_code == "malformed_file"


@pytest.mark.parametrize(
    "extension,content",
    [
        (".docx", make_zip({"[Content_Types].xml": b"types", "word/document.xml": b""})),
        (".xlsx", make_zip({"[Content_Types].xml": b"", "xl/workbook.xml": b"workbook"})),
    ],
)
def test_zero_length_required_zip_member_is_malformed(storage, extension, content):
    document, _ = make_document(storage, content, extension)
    assert validate(storage, document).outcome_code == "malformed_file"


@pytest.mark.parametrize("extension,member", [(".docx", "word/document.xml"), (".xlsx", "xl/workbook.xml")])
def test_duplicate_required_zip_member_is_malformed(storage, extension, member):
    document, _ = make_document(storage, make_zip_with_duplicate_member(member), extension)
    assert validate(storage, document).outcome_code == "malformed_file"


def test_csv_classification_accepts_utf8_prose_without_parsing_structure(storage):
    document, _ = make_document(storage, "This is ordinary UTF-8 prose, not parsed as CSV.".encode(), ".csv")
    assert validate(storage, document).valid


@pytest.mark.parametrize("content", [b"\xff\xfe", b"name,value\n\x81invalid\n", b"name,value\n\x00binary\n"])
def test_csv_classification_rejects_invalid_encoding_and_binary(storage, content):
    document, _ = make_document(storage, content, ".csv")
    assert validate(storage, document).outcome_code == "malformed_file"


def test_csv_classification_accepts_configured_cp1252_fallback(storage):
    content = "name,city\nAndré,Québec\n".encode("cp1252")
    document, _ = make_document(storage, content, ".csv")
    assert validate(storage, document).valid


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows filesystem case handling")
def test_storage_key_case_handling_is_case_insensitive_on_windows(storage):
    document, path = make_document(storage, b"ordinary text", ".csv")
    document.storage_key = document.storage_key.upper()
    result = validate(storage, document)
    assert result.valid and result.path == path.resolve()


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows reparse-point coverage")
def test_windows_directory_reparse_point_escape_is_rejected(storage, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = storage.root / "escaped.csv"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Directory reparse point creation is unavailable: {exc}")
    document = SimpleNamespace(
        tender_id=TENDER_ID,
        storage_key=link.name,
        extension=".csv",
        size_bytes=1,
        sha256=hashlib.sha256(b"x").hexdigest(),
    )
    assert validate(storage, document).outcome_code == "unsafe_storage_key"


def test_missing_source_is_safe(storage):
    document, path = make_document(storage, b"a,b\n1,2\n", ".csv")
    path.unlink()
    assert validate(storage, document).outcome_code == "source_unavailable"


def test_directory_instead_of_regular_file_is_unavailable(storage):
    key = "directory.csv"
    (storage.root / key).mkdir()
    document = SimpleNamespace(
        tender_id=TENDER_ID,
        storage_key=key,
        extension=".csv",
        size_bytes=1,
        sha256=hashlib.sha256(b"x").hexdigest(),
    )
    assert validate(storage, document).outcome_code == "source_unavailable"


@pytest.mark.parametrize("key", ["../outside.csv", "nested/file.csv", "", ".."])
def test_unsafe_storage_keys_are_rejected(storage, key):
    document = SimpleNamespace(
        tender_id=TENDER_ID,
        storage_key=key,
        extension=".csv",
        size_bytes=2,
        sha256=hashlib.sha256(b"a\n").hexdigest(),
    )
    result = validate(storage, document)
    assert result.outcome_code in {"unsafe_storage_key", "invalid_document_metadata"}


def test_symlink_escape_is_rejected(storage, tmp_path):
    outside = tmp_path / "outside.csv"
    outside.write_bytes(b"a,b\n")
    link = storage.root / "linked.csv"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlink creation is unavailable on this platform: {exc}")
    document = SimpleNamespace(
        tender_id=TENDER_ID,
        storage_key=link.name,
        extension=".csv",
        size_bytes=outside.stat().st_size,
        sha256=hashlib.sha256(outside.read_bytes()).hexdigest(),
    )
    assert validate(storage, document).outcome_code == "unsafe_storage_key"


def test_symlink_resolving_inside_private_storage_is_accepted(storage):
    document, original = make_document(storage, b"a,b\n1,2\n", ".csv")
    link = storage.root / "linked.csv"
    try:
        link.symlink_to(original)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlink creation is unavailable on this platform: {exc}")
    document.storage_key = link.name
    result = validate(storage, document)
    assert result.valid and result.path == original.resolve()


def test_staged_copy_is_revalidated(storage):
    document, _ = make_document(storage, b"a,b\n1,2\n", ".csv")
    staged = storage.stage_source(document.storage_key)
    try:
        assert validate_document_source(document, TENDER_ID, storage, MAX_SIZE).valid
        # The stage that will be handed to the handler must still match the
        # upload metadata, even if it changes after the original was checked.
        staged.write_bytes(b"x,y\n1,2\n")
        assert (
            validate_staged_source(staged, document, storage, MAX_SIZE).outcome_code
            == "source_hash_mismatch"
        )
    finally:
        storage.cleanup_source_stage(staged)


def test_size_hash_and_exact_limit(storage):
    limit = MAX_SIZE
    content = b"a" * limit
    document, _ = make_document(storage, content, ".csv")
    assert validate(storage, document, maximum_size=limit).valid

    oversized, _ = make_document(storage, content + b"a", ".csv")
    assert validate(storage, oversized, maximum_size=limit).outcome_code == "resource_limit_exceeded"

    mismatch, _ = make_document(storage, b"a,b\n1,2\n", ".csv", size_bytes=100)
    assert validate(storage, mismatch).outcome_code == "source_size_mismatch"

    bad_hash, _ = make_document(storage, b"a,b\n1,2\n", ".csv", sha256="0" * 64)
    assert validate(storage, bad_hash).outcome_code == "source_hash_mismatch"


@pytest.mark.parametrize(
    "overrides",
    [
        {"storage_key": None},
        {"size_bytes": None},
        {"sha256": None},
        {"sha256": "not-a-sha256"},
        {"extension": "pdf"},
        {"extension": ".PDF"},
        {"tender_id": uuid4()},
    ],
)
def test_invalid_or_inconsistent_metadata_is_rejected(storage, overrides):
    document, _ = make_document(storage, b"a,b\n1,2\n", ".csv")
    for name, value in overrides.items():
        setattr(document, name, value)
    assert validate(storage, document).outcome_code == "invalid_document_metadata"


def test_unknown_extension_is_unsupported(storage):
    document, _ = make_document(storage, b"a,b\n1,2\n", ".txt")
    assert validate(storage, document).outcome_code == "unsupported_format"


def test_limits_are_validated_and_match_approved_values():
    settings = Settings(_env_file=None)
    assert settings.tenderwise_processing_max_documents_per_run == 20
    assert settings.tenderwise_max_file_size_bytes == 50 * 1024 * 1024
    assert settings.tenderwise_max_batch_size_bytes == 200 * 1024 * 1024
    assert MAX_DOCUMENTS_PER_PROCESSING_RUN == settings.tenderwise_processing_max_documents_per_run
    assert ProcessingRunCreate.model_json_schema()["properties"]["document_ids"]["maxItems"] == 20
    with pytest.raises(ValidationError):
        Settings(_env_file=None, tenderwise_processing_max_documents_per_run=21)
