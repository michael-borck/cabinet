"""macOS Finder tag export (Phase 5c, design §5).

Concept names are added as native Finder tags via the
``com.apple.metadata:_kMDItemUserTags`` xattr. This is additive: existing
user tags (including their Finder colours) are preserved, and stale Cabinet
tags are not removed in this slice. Non-macOS platforms report unsupported.
"""

from __future__ import annotations

import ctypes
import errno
import logging
import os
import plistlib
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import DocumentCategory, File, FileCategory
from cabinet.db.repositories import CategoryRepository
from cabinet.schemas import FinderTagsPlan, FinderTagsResult
from cabinet.utils.cloud import is_cloud_placeholder

logger = logging.getLogger(__name__)

TAG_ATTR = "com.apple.metadata:_kMDItemUserTags"
_MAX_SAMPLES = 20

# Python's os module does not expose xattr helpers on macOS; call libc
# directly. The signatures are the Darwin six-argument variants.
_libc = None
if sys.platform == "darwin":
    _libc = ctypes.CDLL(None, use_errno=True)
    _libc.setxattr.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.c_int,
    ]
    _libc.setxattr.restype = ctypes.c_int
    _libc.getxattr.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.c_int,
    ]
    _libc.getxattr.restype = ctypes.c_ssize_t


def _supported() -> bool:
    return sys.platform == "darwin" and _libc is not None


class FinderTagsService:
    """Plans and applies additive Finder tags from document concepts."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def plan(self) -> FinderTagsPlan:
        if not _supported():
            return FinderTagsPlan(
                supported=False,
                message="Finder tags are only supported on macOS.",
            )
        items = await self._items()
        tagged = 0
        skipped = 0
        samples: list[str] = []
        for path, tags in items:
            existing = _existing_names(read_raw_tags(path))
            new_tags = [tag for tag in tags if tag not in existing]
            if new_tags:
                tagged += 1
                if len(samples) < _MAX_SAMPLES:
                    samples.append(f"{path}: {', '.join(new_tags)}")
            else:
                skipped += 1
        return FinderTagsPlan(
            files=len(items), tagged=tagged, skipped=skipped, samples=samples
        )

    async def apply(self) -> FinderTagsResult:
        if not _supported():
            return FinderTagsResult(
                supported=False,
                message="Finder tags are only supported on macOS.",
            )

        items = await self._items()
        tagged = 0
        skipped = 0
        errors: list[str] = []
        for path, tags in items:
            raw = read_raw_tags(path)
            existing = _existing_names(raw)
            new_tags = [tag for tag in tags if tag not in existing]
            if not new_tags:
                skipped += 1
                continue
            try:
                write_raw_tags(path, raw + new_tags)
                tagged += 1
            except OSError as exc:
                errors.append(f"{path}: {exc}")
        return FinderTagsResult(
            tagged=tagged, skipped=skipped, errors=errors[:20]
        )

    async def _items(self) -> list[tuple[str, list[str]]]:
        categories = await CategoryRepository.list_all(self._session)
        names = {
            category.id: category.name
            for category in categories
            if category.status != "merged"
        }

        collected: dict[int, dict] = {}

        def add_row(file_id, path, status, placeholder, category_id):
            if category_id not in names:
                return
            entry = collected.setdefault(
                file_id,
                {
                    "path": path,
                    "status": status,
                    "placeholder": placeholder,
                    "categories": set(),
                },
            )
            entry["categories"].add(category_id)

        document_rows = (
            await self._session.execute(
                select(
                    File.id,
                    File.path,
                    File.status,
                    File.is_placeholder,
                    DocumentCategory.category_id,
                ).join(
                    DocumentCategory,
                    DocumentCategory.document_id == File.document_id,
                )
            )
        ).all()
        for file_id, path, status, placeholder, category_id in document_rows:
            add_row(file_id, path, status, placeholder, category_id)

        instance_rows = (
            await self._session.execute(
                select(
                    File.id,
                    File.path,
                    File.status,
                    File.is_placeholder,
                    FileCategory.category_id,
                ).join(FileCategory, FileCategory.file_id == File.id)
            )
        ).all()
        for file_id, path, status, placeholder, category_id in instance_rows:
            add_row(file_id, path, status, placeholder, category_id)

        items: list[tuple[str, list[str]]] = []
        for entry in collected.values():
            path = entry["path"]
            if entry["status"] == "missing" or entry["placeholder"]:
                continue
            if not os.path.isfile(path) or is_cloud_placeholder(path):
                continue
            tags = sorted(
                {names[category_id] for category_id in entry["categories"]}
            )
            if tags:
                items.append((path, tags))
        return items


# --------------------------------------------------------------------------
# xattr helpers
# --------------------------------------------------------------------------


def read_raw_tags(path: str) -> list[str]:
    try:
        raw = _getxattr(path, TAG_ATTR)
    except OSError:
        return []
    if not raw:
        return []
    try:
        values = plistlib.loads(raw)
    except (ValueError, TypeError):
        logger.debug("Could not parse Finder tags for %s", path, exc_info=True)
        return []
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, str)]


def write_raw_tags(path: str, tags: list[str]) -> None:
    payload = plistlib.dumps(tags, fmt=plistlib.FMT_BINARY)
    _setxattr(path, TAG_ATTR, payload)


def _getxattr(path: str, name: str) -> bytes:
    if _libc is None:
        raise OSError(errno.ENOTSUP, "xattr is unavailable")
    size = _libc.getxattr(
        path.encode(), name.encode(), None, 0, 0, 0
    )
    if size < 0:
        error = ctypes.get_errno()
        if error == errno.ENOATTR:
            return b""
        raise OSError(error, os.strerror(error))
    if size == 0:
        return b""
    buffer = ctypes.create_string_buffer(size)
    written = _libc.getxattr(
        path.encode(), name.encode(), buffer, size, 0, 0
    )
    if written < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return buffer.raw[:written]


def _setxattr(path: str, name: str, value: bytes) -> None:
    if _libc is None:
        raise OSError(errno.ENOTSUP, "xattr is unavailable")
    buffer = ctypes.create_string_buffer(value, len(value))
    result = _libc.setxattr(
        path.encode(), name.encode(), buffer, len(value), 0, 0
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _existing_names(raw: list[str]) -> set[str]:
    # Finder encodes a tag colour as "\n<index>"; compare on the name only.
    return {value.split("\n", 1)[0] for value in raw}
