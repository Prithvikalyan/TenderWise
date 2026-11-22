"""Private document storage with a database-reconciled deletion journal."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import UUID, uuid4

from fastapi import UploadFile
from botocore.config import Config as BotoConfig
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.tender_document import TenderDocument

CHUNK_SIZE = 1024 * 1024
_STAGING_THREAD_LOCK = threading.RLock()
_ACTIVE_SOURCE_STAGES: set[Path] = set()


class StorageError(Exception):
    """A safe-to-report storage operation failure."""


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[bool]:
    """Lock a journal file across processes; process death releases the lock."""
    with path.open("r+b") as stream:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
            try:
                yield True
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class DocumentStorage:
    def __init__(self, root: Path | None = None, *, s3_client=None) -> None:
        settings = get_settings()
        self.root = (root or settings.tenderwise_storage_dir).expanduser().resolve()
        self.is_remote = settings.tenderwise_storage_backend == "s3"
        self.bucket = settings.neon_storage_bucket
        self.s3 = None
        if self.is_remote:
            required = {
                "NEON_STORAGE_BUCKET": self.bucket,
                "AWS_ENDPOINT_URL_S3": settings.aws_endpoint_url_s3,
                "AWS_REGION": settings.aws_region,
                "AWS_ACCESS_KEY_ID": settings.aws_access_key_id,
                "AWS_SECRET_ACCESS_KEY": settings.aws_secret_access_key,
            }
            missing = [name for name, value in required.items() if not value or not str(value).strip()]
            if missing:
                raise StorageError("Object storage configuration is incomplete: " + ", ".join(missing))
            self.s3 = s3_client or self._create_s3_client(settings)
        self.root.mkdir(parents=True, exist_ok=True)
        self.staging = self.root / ".staging"
        self.quarantine = self.root / ".quarantine"
        self.staging.mkdir(exist_ok=True)
        self.quarantine.mkdir(exist_ok=True)
        self._staging_guard_path = self.staging / ".cleanup.lock"
        self._ensure_lock_file(self._staging_guard_path)
        self._active_source_locks: dict[Path, object] = {}
        self._journal_lock_path = self.quarantine / ".recovery.lock"
        self._journal_lock_path.touch(exist_ok=True)
        if self._journal_lock_path.stat().st_size == 0:
            with self._journal_lock_path.open("ab") as lock_file:
                lock_file.write(b"0")
                lock_file.flush()
                os.fsync(lock_file.fileno())
        self._active_journal_locks = {}
        self.cleanup_abandoned_source_stages()

    @staticmethod
    def _create_s3_client(settings):
        """Create an S3-compatible client for Neon Object Storage."""
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=settings.aws_endpoint_url_s3,
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    @staticmethod
    def _ensure_lock_file(path: Path) -> None:
        with path.open("a+b") as lock_file:
            os.chmod(path, 0o600)
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"0")
                lock_file.flush()
                os.fsync(lock_file.fileno())

    @staticmethod
    def _enter_file_lock(path: Path, *, wait: bool):
        while True:
            manager = _exclusive_file_lock(path)
            acquired = manager.__enter__()
            if acquired:
                return manager
            manager.__exit__(None, None, None)
            if not wait:
                return None
            time.sleep(0.05)

    def _enter_staging_guard(self, *, wait: bool):
        while True:
            _STAGING_THREAD_LOCK.acquire()
            manager = self._enter_file_lock(self._staging_guard_path, wait=wait)
            if manager is not None:
                return manager
            _STAGING_THREAD_LOCK.release()
            if not wait:
                return None
            time.sleep(0.05)

    @staticmethod
    def _leave_staging_guard(manager) -> None:
        manager.__exit__(None, None, None)
        _STAGING_THREAD_LOCK.release()

    def _acquire_journal_lock(self, *, wait: bool) -> bool:
        while True:
            manager = _exclusive_file_lock(self._journal_lock_path)
            acquired = manager.__enter__()
            if acquired:
                self._active_journal_locks[threading.get_ident()] = manager
                return True
            manager.__exit__(None, None, None)
            if not wait:
                return False
            time.sleep(0.05)

    def _release_journal_lock(self) -> None:
        manager = self._active_journal_locks.pop(threading.get_ident(), None)
        if manager is not None:
            manager.__exit__(None, None, None)

    def resolve_key(self, key: str) -> Path:
        if Path(key).name != key or key in {"", ".", ".."}:
            raise StorageError("Invalid storage reference")
        candidate = (self.root / key).resolve()
        if candidate.parent != self.root:
            raise StorageError("Invalid storage reference")
        return candidate

    def materialize(self, key: str) -> Path:
        """Return a local path for a stored object, downloading it when remote."""
        local_path = self.resolve_key(key)
        if not self.is_remote:
            return local_path
        temporary = self.staging / f"read-{uuid4().hex}.part"
        try:
            self.s3.download_file(self.bucket, key, str(temporary))
            os.chmod(temporary, 0o600)
            return temporary
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise StorageError("Could not retrieve stored document") from exc

    def cleanup_materialized(self, path: Path) -> None:
        """Delete a temporary materialized copy after validation or response."""
        try:
            resolved = path.resolve()
            if resolved.parent == self.staging.resolve() and resolved.name.startswith("read-"):
                resolved.unlink(missing_ok=True)
        except OSError:
            # Temporary cleanup is best-effort; no source object is affected.
            pass

    def stage(self, upload: UploadFile, maximum: int) -> tuple[Path, str, int, bytes]:
        path = self.staging / f"{uuid4().hex}.part"
        digest = hashlib.sha256()
        total = 0
        prefix = b""
        try:
            with path.open("xb") as output:
                os.chmod(path, 0o600)
                while chunk := upload.file.read(CHUNK_SIZE):
                    total += len(chunk)
                    if total > maximum:
                        raise OverflowError
                    if len(prefix) < 16:
                        prefix += chunk[: 16 - len(prefix)]
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            self._fsync_dir(self.staging)
            if total == 0:
                raise ValueError("empty")
            return path, digest.hexdigest(), total, prefix
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def promote(self, staged: Path, extension: str) -> str:
        key = f"{uuid4().hex}{extension}"
        if self.is_remote:
            try:
                self.s3.upload_file(str(staged), self.bucket, key)
            except Exception as exc:
                raise StorageError("Could not store uploaded document") from exc
            staged.unlink(missing_ok=True)
            self._fsync_dir(self.staging)
            return key
        destination = self.resolve_key(key)
        if destination.exists():
            raise StorageError("Storage collision")
        os.link(staged, destination)
        self._fsync_dir(self.root)
        staged.unlink()
        self._fsync_dir(self.staging)
        return key

    def stage_source(self, key: str) -> Path:
        """Copy a source to a unique private work file while caller holds its row lock."""
        source = self.resolve_key(key)
        staged = self.staging / f"work-{uuid4().hex}.part"
        lease = staged.with_suffix(".lease")
        guard = self._enter_staging_guard(wait=True)
        lease_lock = None
        try:
            self._ensure_lock_file(lease)
            lease_lock = self._enter_file_lock(lease, wait=False)
            if lease_lock is None:
                raise StorageError("Could not acquire source staging lease")
            if self.is_remote:
                try:
                    self.s3.download_file(self.bucket, key, str(staged))
                    os.chmod(staged, 0o600)
                    # Open read/write before fsync. On Windows, fsync on a
                    # read-only descriptor can fail with EINVAL even though
                    # the S3 download completed successfully.
                    with staged.open("r+b") as output:
                        os.fsync(output.fileno())
                except Exception as exc:
                    raise StorageError("Could not retrieve document for processing") from exc
            else:
                with source.open("rb") as input_file, staged.open("xb") as output:
                    os.chmod(staged, 0o600)
                    while chunk := input_file.read(CHUNK_SIZE):
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            self._fsync_dir(self.staging)
            resolved = staged.resolve()
            self._active_source_locks[resolved] = lease_lock
            _ACTIVE_SOURCE_STAGES.add(resolved)
            lease_lock = None
            return staged
        except BaseException:
            if lease_lock is not None:
                lease_lock.__exit__(None, None, None)
                lease_lock = None
            staged.unlink(missing_ok=True)
            lease.unlink(missing_ok=True)
            raise
        finally:
            if lease_lock is not None:
                lease_lock.__exit__(None, None, None)
            self._leave_staging_guard(guard)

    def cleanup_abandoned_source_stages(self) -> int:
        """Remove work files only after acquiring their per-stage lease lock."""
        guard = self._enter_staging_guard(wait=True)
        removed = 0
        try:
            for staged in self.staging.glob("work-*.part"):
                resolved = staged.resolve()
                if resolved in _ACTIVE_SOURCE_STAGES:
                    continue
                lease = staged.with_suffix(".lease")
                if not lease.exists():
                    # An unleased file may belong to a worker from an older
                    # process version; preserve it because ownership is unknown.
                    continue
                lease_lock = self._enter_file_lock(lease, wait=False)
                if lease_lock is None:
                    continue
                try:
                    lease_lock.__exit__(None, None, None)
                    staged.unlink(missing_ok=True)
                    lease.unlink(missing_ok=True)
                    removed += 1
                    self._fsync_dir(self.staging)
                except OSError:
                    # Leave any remaining entry for a later cleanup attempt.
                    continue
            for lease in self.staging.glob("work-*.lease"):
                staged = lease.with_suffix(".part")
                if staged.exists():
                    continue
                lease_lock = self._enter_file_lock(lease, wait=False)
                if lease_lock is None:
                    continue
                try:
                    lease_lock.__exit__(None, None, None)
                    lease.unlink(missing_ok=True)
                    self._fsync_dir(self.staging)
                except OSError:
                    continue
            return removed
        finally:
            self._leave_staging_guard(guard)

    def remove(self, key: str) -> None:
        self.resolve_key(key)
        if self.is_remote:
            try:
                self.s3.delete_object(Bucket=self.bucket, Key=key)
            except Exception as exc:
                raise StorageError("Could not delete stored document") from exc
            return
        self.resolve_key(key).unlink(missing_ok=True)

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write_json_atomically(self, manifest: Path, payload: dict) -> None:
        temporary = manifest.with_suffix(".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as output:
                os.chmod(temporary, 0o600)
                json.dump(payload, output, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, manifest)
            self._fsync_dir(self.quarantine)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def quarantine_documents(
        self, documents: list[tuple[UUID, UUID, str]]
    ) -> tuple[Path | None, list[tuple[Path, Path]]]:
        """Journal and atomically quarantine local sources before metadata deletion."""
        if self.is_remote:
            raise StorageError("Remote objects must be deleted after the database transaction commits")
        if not documents:
            return None, []
        if not self._acquire_journal_lock(wait=True):
            raise StorageError("Another source deletion is being recovered")
        manifest = self.quarantine / f"delete-{uuid4().hex}.json"
        records = [
            {
                "document_id": str(document_id),
                "tender_id": str(tender_id),
                "source": self.resolve_key(key).name,
                "held": f"{uuid4().hex}.held",
            }
            for document_id, tender_id, key in documents
        ]
        payload = {"version": 1, "state": "deleting", "files": records}
        moved: list[tuple[Path, Path]] = []
        try:
            self._write_json_atomically(manifest, payload)
            for record in records:
                source = self.root / record["source"]
                held = self.quarantine / record["held"]
                try:
                    os.replace(source, held)
                except FileNotFoundError:
                    continue
                self._fsync_dir(self.root)
                self._fsync_dir(self.quarantine)
                moved.append((source, held))
            return manifest, moved
        except BaseException:
            try:
                if manifest.exists():
                    self.restore_operation(manifest)
            finally:
                self._release_journal_lock()
            raise

    def restore_operation(self, manifest: Path) -> None:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        for record in reversed(payload["files"]):
            source = self.resolve_key(record["source"])
            held_name = record["held"]
            if Path(held_name).name != held_name or not held_name.endswith(".held"):
                raise StorageError("Invalid deletion journal")
            held = self.quarantine / held_name
            if held.exists() and not source.exists():
                os.replace(held, source)
                self._fsync_dir(self.root)
                self._fsync_dir(self.quarantine)
        manifest.unlink(missing_ok=True)
        self._fsync_dir(self.quarantine)

    def purge_operation(self, manifest: Path) -> None:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        for record in payload["files"]:
            held_name = record["held"]
            if Path(held_name).name != held_name or not held_name.endswith(".held"):
                raise StorageError("Invalid deletion journal")
            (self.quarantine / held_name).unlink(missing_ok=True)
            self._fsync_dir(self.quarantine)
        manifest.unlink(missing_ok=True)
        self._fsync_dir(self.quarantine)

    def finish_operation(self, manifest: Path, *, committed: bool) -> None:
        try:
            if committed:
                self.purge_operation(manifest)
            else:
                self.restore_operation(manifest)
        finally:
            self._release_journal_lock()

    def resolve_operation(self, manifest: Path, session_factory) -> None:
        """Use committed document rows to resolve a deletion whose commit result is uncertain."""
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            document_ids = [UUID(record["document_id"]) for record in payload["files"]]
            with session_factory() as session:
                present = set(
                    session.scalars(
                        select(TenderDocument.id).where(TenderDocument.id.in_(document_ids))
                    ).all()
                ) if document_ids else set()
                session.rollback()
            if present == set(document_ids):
                self.restore_operation(manifest)
            elif not present:
                self.purge_operation(manifest)
            # Mixed rows or a failed query are uncertain; leave the journal untouched.
        except Exception:
            pass
        finally:
            self._release_journal_lock()

    def recover_pending_deletions(self, session_factory) -> None:
        """Reconcile local journals with committed document rows."""
        if self.is_remote:
            return
        if not self._acquire_journal_lock(wait=False):
            return
        try:
            self._recover_journals(session_factory)
        finally:
            self._release_journal_lock()

    def _recover_journals(self, session_factory) -> None:
        for manifest in self.quarantine.glob("delete-*.json"):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                records = payload.get("files")
                if payload.get("version") != 1 or not isinstance(records, list):
                    continue
                document_ids = [UUID(record["document_id"]) for record in records]
                if len(document_ids) != len(set(document_ids)):
                    continue
                with session_factory() as session:
                    present = set(
                        session.scalars(
                            select(TenderDocument.id).where(TenderDocument.id.in_(document_ids))
                        ).all()
                    ) if document_ids else set()
                    session.rollback()
                if present == set(document_ids):
                    self.restore_operation(manifest)
                elif not present:
                    self.purge_operation(manifest)
                # A partial set is impossible under the deletion transaction;
                # preserve the journal for human/operational investigation.
            except Exception:
                # Database failure, malformed journal, or filesystem failure: retry later.
                continue
        self._recover_legacy_quarantines(session_factory)
        self.retry_pending_purges()

    def _recover_legacy_quarantines(self, session_factory) -> None:
        """Resolve pre-v1 quarantine manifests using their unique storage keys."""
        for manifest in self.quarantine.glob("quarantine-*.json"):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                records = payload.get("files")
                if payload.get("state") != "quarantined" or not isinstance(records, list):
                    continue
                sources = [self.resolve_key(record["source"]).name for record in records]
                held_names = [record["held"] for record in records]
                if any(Path(name).name != name or not name.endswith(".held") for name in held_names):
                    continue
                with session_factory() as session:
                    present = set(
                        session.scalars(
                            select(TenderDocument.storage_key).where(
                                TenderDocument.storage_key.in_(sources)
                            )
                        ).all()
                    ) if sources else set()
                    session.rollback()
                if present == set(sources):
                    for record in reversed(records):
                        source = self.resolve_key(record["source"])
                        held = self.quarantine / record["held"]
                        if held.exists() and not source.exists():
                            os.replace(held, source)
                    self._fsync_dir(self.root)
                    self._fsync_dir(self.quarantine)
                    manifest.unlink(missing_ok=True)
                elif not present:
                    for name in held_names:
                        (self.quarantine / name).unlink(missing_ok=True)
                    manifest.unlink(missing_ok=True)
                    self._fsync_dir(self.quarantine)
            except Exception:
                continue

    def quarantine_keys(self, keys: list[str]) -> list[tuple[Path, Path]]:
        """Compatibility helper for non-database callers; no recoverable metadata."""
        moved: list[tuple[Path, Path]] = []
        try:
            for key in keys:
                source = self.resolve_key(key)
                held = self.quarantine / f"{uuid4().hex}.held"
                try:
                    os.replace(source, held)
                except FileNotFoundError:
                    continue
                self._fsync_dir(self.root)
                self._fsync_dir(self.quarantine)
                moved.append((source, held))
            return moved
        except BaseException:
            for source, held in reversed(moved):
                if held.exists():
                    os.replace(held, source)
            raise

    def purge_with_retry(self, moved: list[tuple[Path, Path]]) -> None:
        for _source, held in moved:
            held.unlink(missing_ok=True)
        self._fsync_dir(self.quarantine)

    def retry_pending_purges(self) -> None:
        """Legacy purge journal compatibility; new deletions use database reconciliation."""
        for manifest in self.quarantine.glob("purge-*.json"):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                for record in payload["files"]:
                    name = record["held"]
                    if Path(name).name != name or not name.endswith(".held"):
                        raise ValueError("Invalid purge manifest")
                    (self.quarantine / name).unlink(missing_ok=True)
                manifest.unlink(missing_ok=True)
                self._fsync_dir(self.quarantine)
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue

    def cleanup_staging(self, paths: list[Path]) -> None:
        for path in paths:
            path.unlink(missing_ok=True)

    def cleanup_source_stage(self, path: Path) -> None:
        if path.parent != self.staging or not path.name.startswith("work-"):
            return
        resolved = path.resolve()
        guard = self._enter_staging_guard(wait=True)
        try:
            lease_lock = self._active_source_locks.pop(resolved, None)
            if lease_lock is None:
                return
            _ACTIVE_SOURCE_STAGES.discard(resolved)
            lease_lock.__exit__(None, None, None)
            path.unlink(missing_ok=True)
            path.with_suffix(".lease").unlink(missing_ok=True)
            self._fsync_dir(self.staging)
        finally:
            self._leave_staging_guard(guard)
