"""Filesystem scanning and reconciliation service."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import Document, File, Job, Source
from cabinet.db.repositories import DocumentRepository
from cabinet.utils.hashing import compute_file_hash, hashes_equal, prefixed_hash

logger = logging.getLogger(__name__)

# Directories that are never worth categorizing: development artifacts and
# OS metadata. Matched by exact name at any depth (dot-prefixed names are
# already skipped separately). Users cannot re-include these; per-source
# ``exclude_patterns`` only add to the list.
DEFAULT_EXCLUDED_DIRS = frozenset(
    {
        "node_modules",
        "__pycache__",
        "venv",
        "virtualenv",
        "dist",
        "build",
        "target",
        "vendor",
        "bower_components",
        "coverage",
        "htmlcov",
        "site-packages",
        "__MACOSX",
        "$RECYCLE.BIN",
        "System Volume Information",
    }
)

# Junk files skipped by exact name (case-insensitive).
DEFAULT_EXCLUDED_FILES = frozenset({"thumbs.db", "desktop.ini"})

# Office lock/temp files ("~$Report.docx").
_TEMP_FILE_PREFIX = "~$"


def _matches_patterns(name: str, rel_path: str, patterns: Sequence[str]) -> bool:
    """Return True when a user-supplied glob matches the entry.

    Patterns are tested against both the bare name (``drafts``, ``*.log``)
    and the path relative to the source root with ``/`` separators
    (``projects/old*``), so simple patterns apply at any depth.
    """
    return any(
        fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel_path, pattern)
        for pattern in patterns
    )


def _dir_excluded(name: str, rel_path: str, patterns: Sequence[str]) -> bool:
    return (
        name.startswith(".")
        or name in DEFAULT_EXCLUDED_DIRS
        or _matches_patterns(name, rel_path, patterns)
    )


def _file_excluded(name: str, rel_path: str, patterns: Sequence[str]) -> bool:
    return (
        name.startswith(".")
        or name.startswith(_TEMP_FILE_PREFIX)
        or name.lower() in DEFAULT_EXCLUDED_FILES
        or _matches_patterns(name, rel_path, patterns)
    )


class ScannerService:
    """Scans source directories and reconciles filesystem state with the database."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scan_source(
        self, source_id: int, mode: str = "quick_sync", job_id: int | None = None
    ) -> Job:
        """Scan a source directory and reconcile with the database.

        Args:
            source_id: The ID of the source to scan.
            mode: Either ``"quick_sync"`` (only new/changed files) or
                ``"full_reindex"`` (reprocess everything).
            job_id: An existing :class:`Job` to track this scan (created by the
                scan endpoint). When omitted, a new job is created.

        Returns:
            The :class:`Job` record tracking this scan.

        Raises:
            ValueError: If the source or job does not exist or the path is invalid.
        """
        # Fetch the source ---------------------------------------------------
        result = await self._session.execute(
            select(Source).where(Source.id == source_id)
        )
        source = result.scalar_one_or_none()
        if source is None:
            raise ValueError(f"Source with id={source_id} not found")

        if not os.path.isdir(source.path):
            raise ValueError(
                f"Source path does not exist or is not a directory: {source.path!r}"
            )

        # Track progress on the caller's job, or create one -------------------
        if job_id is not None:
            job = await self._session.get(Job, job_id)
            if job is None:
                raise ValueError(f"Job with id={job_id} not found")
            job.status = "running"
            job.total_files = 0
            job.processed_files = 0
        else:
            job = Job(
                job_type=mode,
                status="running",
                source_id=source_id,
                total_files=0,
                processed_files=0,
                error_count=0,
            )
            self._session.add(job)
        await self._session.flush()  # ensure job.id is populated

        try:
            # Walk the filesystem ---------------------------------------------
            found_files = await self._walk_filesystem(
                source.path, source.recursive, source.exclude_patterns or []
            )
            job.total_files = len(found_files)

            # Reconcile -------------------------------------------------------
            counts = await self._reconcile(source_id, found_files, mode)
            new_count = counts["new"]
            missing_count = counts["missing"]
            changed_count = counts["changed"]
            job.moved_count = counts["moved"]
            job.processed_files = (
                new_count + missing_count + changed_count + counts["moved"]
            )
            job.status = "completed"

            # Update source's last_scanned_at ----------------------------------
            source.last_scanned_at = _naive_utc_now()

            logger.info(
                "Scan completed for source %d (%s): "
                "new=%d, missing=%d, changed=%d, moved=%d, total_found=%d",
                source_id,
                mode,
                new_count,
                missing_count,
                changed_count,
                counts["moved"],
                len(found_files),
            )
        except Exception:
            job.status = "failed"
            job.error_count += 1
            logger.exception("Scan failed for source %d", source_id)
            raise
        finally:
            await self._session.flush()

        return job

    # ------------------------------------------------------------------
    # Filesystem walking
    # ------------------------------------------------------------------

    async def _walk_filesystem(
        self, root_path: str, recursive: bool, exclude_patterns: Sequence[str] = ()
    ) -> list[dict[str, Any]]:
        """Walk the filesystem and collect file metadata.

        Skipped entries: hidden files and directories (names starting with
        ``"."``), the built-in default exclusions (``node_modules``,
        ``__pycache__``, junk files, ...), and anything matching the
        source's ``exclude_patterns`` globs.

        Returns:
            A list of dicts with keys: ``path``, ``filename``, ``extension``,
            ``size_bytes``, ``file_modified_at``.
        """

        def _rel(full_path: str) -> str:
            return os.path.relpath(full_path, root_path).replace(os.sep, "/")

        def _scan() -> list[dict[str, Any]]:
            files: list[dict[str, Any]] = []

            if recursive:
                for dirpath, dirnames, filenames in os.walk(root_path):
                    # Prune in-place so os.walk won't descend into them.
                    dirnames[:] = [
                        d
                        for d in dirnames
                        if not _dir_excluded(
                            d, _rel(os.path.join(dirpath, d)), exclude_patterns
                        )
                    ]
                    for fname in filenames:
                        full_path = os.path.join(dirpath, fname)
                        if _file_excluded(fname, _rel(full_path), exclude_patterns):
                            continue
                        info = _file_info(full_path, fname)
                        if info is not None:
                            files.append(info)
            else:
                try:
                    entries = os.scandir(root_path)
                except OSError as exc:
                    logger.warning("Cannot scan directory %s: %s", root_path, exc)
                    return files
                with entries:
                    for entry in entries:
                        if _file_excluded(
                            entry.name, entry.name, exclude_patterns
                        ) or not entry.is_file(follow_symlinks=False):
                            continue
                        info = _file_info(entry.path, entry.name)
                        if info is not None:
                            files.append(info)

            return files

        return await asyncio.to_thread(_scan)

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    async def _reconcile(
        self,
        source_id: int,
        found_files: list[dict[str, Any]],
        mode: str,
    ) -> dict[str, int]:
        """Compare filesystem state with the database and apply changes.

        Identity resolution follows design §3.2: exact path, then
        corroborated inode (move), then content hash (attach to the
        existing document as a move or a copy), else genuinely new.

        Returns:
            Counts dict with ``new``, ``missing``, ``changed``, ``moved``.
        """
        result = await self._session.execute(
            select(File).where(File.source_id == source_id)
        )
        existing_files: list[File] = list(result.scalars().all())
        existing_by_path: dict[str, File] = {f.path: f for f in existing_files}

        found_by_path: dict[str, dict[str, Any]] = {f["path"]: f for f in found_files}
        found_paths = set(found_by_path)
        existing_paths = set(existing_by_path)

        counts = {"new": 0, "missing": 0, "changed": 0, "moved": 0}
        # Existing instance ids matched to something found on disk; the
        # remainder become "missing" in pass C.
        live_ids: set[int] = set()

        # --- Full reindex: re-queue everything present (per instance, as
        # before) and make sure every file has a document for the pipeline.
        if mode == "full_reindex":
            for db_file in existing_files:
                if db_file.path in found_paths:
                    await DocumentRepository.ensure_for_file(self._session, db_file)
                    db_file.status = "pending"
                    doc = await self._session.get(Document, db_file.document_id)
                    if doc is not None:
                        doc.status = "pending"
                    counts["changed"] += 1

        # --- Pass A: exact path matches -----------------------------------
        for path in sorted(existing_paths & found_paths):
            db_file = existing_by_path[path]
            info = found_by_path[path]
            live_ids.add(db_file.id)
            # Always refresh inode/device: cheap, and it arms zero-read
            # rename detection for legacy rows that predate the columns.
            db_file.device_id = info.get("device_id")
            db_file.inode = info.get("inode")
            if mode == "full_reindex":
                continue

            was_missing = db_file.status == "missing"
            fs_mtime = info["file_modified_at"]
            mtime_changed = db_file.file_modified_at is None or (
                fs_mtime is not None and fs_mtime != db_file.file_modified_at
            )

            if not mtime_changed and db_file.content_hash is not None:
                # Fast path (unchanged): just guarantee document linkage.
                await DocumentRepository.ensure_for_file(self._session, db_file)
                if was_missing:
                    db_file.status = await self._document_status(db_file)
                    counts["changed"] += 1
                continue

            if not mtime_changed and db_file.content_hash is None:
                # Lazy hash backfill for pre-1a rows. Attaching the hash may
                # merge this file's provisional document into a known one;
                # status is left alone unless it was missing.
                digest = await self._hash_file(path)
                if digest is not None:
                    await DocumentRepository.attach_hash(
                        self._session, db_file, digest
                    )
                if was_missing:
                    db_file.status = await self._document_status(db_file)
                    counts["changed"] += 1
                continue

            # Mtime moved: only the hash can say whether content changed.
            digest = await self._hash_file(path)
            if digest is None:
                continue
            if hashes_equal(digest, db_file.content_hash):
                db_file.file_modified_at = fs_mtime
                await DocumentRepository.ensure_for_file(self._session, db_file)
                if was_missing:
                    db_file.status = await self._document_status(db_file)
                    counts["changed"] += 1
                continue

            # Content changed at this path: the instance now sights the
            # (possibly already-known) document for the new hash.
            doc = await DocumentRepository.attach_hash(self._session, db_file, digest)
            db_file.size_bytes = info["size_bytes"]
            db_file.file_modified_at = fs_mtime
            # A document that is already categorized needs no new work;
            # anything else (re-)enters the pipeline at document level.
            if doc.status != "categorized":
                await DocumentRepository.set_status(self._session, doc, "pending")
            db_file.status = doc.status
            counts["changed"] += 1

        # --- Pass B: new paths — move (inode), attach (hash), or new -----
        unmatched = [f for f in existing_files if f.id not in live_ids]
        for path in sorted(found_paths - existing_paths):
            info = found_by_path[path]

            moved = self._inode_move_candidate(unmatched, live_ids, info)
            if moved is not None:
                self._apply_sighting(moved, info)
                if moved.status == "missing":
                    moved.status = await self._document_status(moved)
                live_ids.add(moved.id)
                counts["moved"] += 1
                continue

            digest = await self._hash_file(path)
            doc = (
                await DocumentRepository.get_by_hash(self._session, digest)
                if digest is not None
                else None
            )

            # Move sub-case: exactly one unmatched instance already sights
            # this content (by document or by a legacy stored hash).
            move_candidates = [
                f
                for f in unmatched
                if f.id not in live_ids
                and digest is not None
                and (
                    (doc is not None and f.document_id == doc.id)
                    or hashes_equal(digest, f.content_hash)
                )
            ]
            if len(move_candidates) == 1:
                moved = move_candidates[0]
                self._apply_sighting(moved, info)
                doc = await DocumentRepository.attach_hash(
                    self._session, moved, digest  # type: ignore[arg-type]
                )
                moved.status = doc.status
                live_ids.add(moved.id)
                counts["moved"] += 1
                continue

            # Copy of known content, or genuinely new content: a new
            # instance either way; only new content needs pipeline work.
            new_file = File(
                source_id=source_id,
                path=info["path"],
                filename=info["filename"],
                extension=info["extension"],
                size_bytes=info["size_bytes"],
                file_modified_at=info["file_modified_at"],
                device_id=info.get("device_id"),
                inode=info.get("inode"),
                status="pending",
            )
            self._session.add(new_file)
            await self._session.flush()
            if digest is not None:
                doc = await DocumentRepository.attach_hash(
                    self._session, new_file, digest
                )
                new_file.status = doc.status  # categorized copies stay done
            else:
                await DocumentRepository.ensure_for_file(self._session, new_file)
            live_ids.add(new_file.id)
            counts["new"] += 1

        # --- Pass C: whatever wasn't matched is missing (tombstone) -------
        for db_file in existing_files:
            if db_file.id not in live_ids and db_file.status != "missing":
                db_file.status = "missing"
                counts["missing"] += 1

        await self._session.flush()
        return counts

    # ------------------------------------------------------------------
    # Reconciliation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _inode_move_candidate(
        unmatched: list[File], live_ids: set[int], info: dict[str, Any]
    ) -> File | None:
        """Find a rename/move by corroborated inode (design §3.3).

        Inode alone is never trusted: device must match, the inode must be
        non-zero, and size + mtime must equal the recorded sighting. A
        filesystem with unstable/zero inodes (FAT/exFAT) simply yields no
        candidate and falls through to hashing.
        """
        inode = info.get("inode")
        device_id = info.get("device_id")
        if not inode or device_id is None:
            return None
        matches = [
            f
            for f in unmatched
            if f.id not in live_ids
            and f.inode == inode
            and f.device_id == device_id
            and f.size_bytes == info["size_bytes"]
            and f.file_modified_at is not None
            and f.file_modified_at == info["file_modified_at"]
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _apply_sighting(file: File, info: dict[str, Any]) -> None:
        """Record a new location for an existing instance."""
        file.path = info["path"]
        file.filename = info["filename"]
        file.extension = info["extension"]
        file.size_bytes = info["size_bytes"]
        file.file_modified_at = info["file_modified_at"]
        file.device_id = info.get("device_id")
        file.inode = info.get("inode")

    async def _document_status(self, file: File) -> str:
        """The status a restored instance should mirror."""
        if file.document_id is None:
            return "pending"
        doc = await self._session.get(Document, file.document_id)
        return doc.status if doc is not None else "pending"

    @staticmethod
    async def _hash_file(path: str) -> str | None:
        """Prefixed content hash, or None when the file can't be read."""
        try:
            return prefixed_hash(await compute_file_hash(path))
        except OSError:
            logger.warning("Could not hash %s; treating as unhashed", path)
            return None


# --------------------------------------------------------------------------
# Module-level helpers
# --------------------------------------------------------------------------

def _file_info(full_path: str, filename: str) -> dict[str, Any] | None:
    """Build a metadata dict for a single file, or *None* on OS errors."""
    try:
        stat = os.stat(full_path)
    except OSError:
        return None

    _, ext = os.path.splitext(filename)
    return {
        "path": full_path,
        "filename": filename,
        "extension": ext.lower() if ext else None,
        "size_bytes": stat.st_size,
        # Identity hints for rename detection; 0/unstable on some
        # filesystems, which the reconcile corroboration handles (§3.3).
        "device_id": stat.st_dev,
        "inode": stat.st_ino or None,
        # Naive UTC: the DateTime columns are timezone-less, and an aware
        # value never compares equal to the naive one read back from the DB,
        # which made every sync rehash every file.
        "file_modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).replace(
            tzinfo=None
        ),
    }


def _naive_utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
