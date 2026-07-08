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

from cabinet.db.models import File, Job, Source
from cabinet.utils.hashing import compute_file_hash

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
            new_count, missing_count, changed_count = await self._reconcile(
                source_id, found_files, mode
            )
            job.processed_files = new_count + missing_count + changed_count
            job.status = "completed"

            # Update source's last_scanned_at ----------------------------------
            source.last_scanned_at = _naive_utc_now()

            logger.info(
                "Scan completed for source %d (%s): "
                "new=%d, missing=%d, changed=%d, total_found=%d",
                source_id,
                mode,
                new_count,
                missing_count,
                changed_count,
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
    ) -> tuple[int, int, int]:
        """Compare filesystem state with the database and apply changes.

        Returns:
            ``(new_count, missing_count, changed_count)``
        """
        # Load existing DB files for this source ------------------------------
        result = await self._session.execute(
            select(File).where(File.source_id == source_id)
        )
        existing_files: list[File] = list(result.scalars().all())
        existing_by_path: dict[str, File] = {f.path: f for f in existing_files}

        found_paths: set[str] = {f["path"] for f in found_files}
        found_by_path: dict[str, dict[str, Any]] = {f["path"]: f for f in found_files}

        new_count = 0
        missing_count = 0
        changed_count = 0

        # --- Full-reindex shortcut: reset all existing files to pending ------
        if mode == "full_reindex":
            for db_file in existing_files:
                if db_file.path in found_paths:
                    db_file.status = "pending"
                    changed_count += 1

        # --- New files (on filesystem but not in DB) -------------------------
        existing_paths = set(existing_by_path.keys())
        for path in found_paths - existing_paths:
            info = found_by_path[path]
            new_file = File(
                source_id=source_id,
                path=info["path"],
                filename=info["filename"],
                extension=info["extension"],
                size_bytes=info["size_bytes"],
                file_modified_at=info["file_modified_at"],
                status="pending",
            )
            self._session.add(new_file)
            new_count += 1

        # --- Missing files (in DB but not on filesystem) ---------------------
        for path in existing_paths - found_paths:
            db_file = existing_by_path[path]
            if db_file.status != "missing":
                db_file.status = "missing"
                missing_count += 1

        # --- Existing files: check for modifications -------------------------
        if mode != "full_reindex":
            for path in existing_paths & found_paths:
                db_file = existing_by_path[path]
                fs_info = found_by_path[path]

                # Compare modification time
                fs_mtime = fs_info["file_modified_at"]
                if db_file.file_modified_at is None or (
                    fs_mtime is not None
                    and fs_mtime != db_file.file_modified_at
                ):
                    # Mtime differs -- check content hash to be sure
                    new_hash = await compute_file_hash(path)
                    if new_hash != db_file.content_hash:
                        db_file.status = "pending"
                        db_file.content_hash = new_hash
                        db_file.size_bytes = fs_info["size_bytes"]
                        db_file.file_modified_at = fs_mtime
                        changed_count += 1
                    else:
                        # Hash matches -- just update mtime
                        db_file.file_modified_at = fs_mtime

                # Ensure a previously-missing file that reappears is re-queued
                if db_file.status == "missing":
                    db_file.status = "pending"
                    changed_count += 1

        await self._session.flush()
        return new_count, missing_count, changed_count


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
        # Naive UTC: the DateTime columns are timezone-less, and an aware
        # value never compares equal to the naive one read back from the DB,
        # which made every sync rehash every file.
        "file_modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).replace(
            tzinfo=None
        ),
    }


def _naive_utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
