"""Materialized symlink-tree export (Phase 5b, design §4).

Projects the concept tree (or one saved view) into a directory of symlinks.
The export is non-destructive: existing files are never overwritten, and only
links recorded in ``.cabinet-export.json`` are refreshed or removed.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import Category, DocumentCategory, File
from cabinet.db.repositories import CategoryRepository, ViewRepository
from cabinet.schemas import (
    SymlinkTreeAction,
    SymlinkTreePlan,
    SymlinkTreeRequest,
    SymlinkTreeResult,
    ViewQuery,
)
from cabinet.services.concepts import concept_path
from cabinet.services.views import ViewQueryService
from cabinet.utils.text import make_slug

logger = logging.getLogger(__name__)

MANIFEST_NAME = ".cabinet-export.json"
MANIFEST_VERSION = 1
_ACTION_SAMPLE = 100
_MAX_VIEW_FILES = 5000
_PAGE_SIZE = 500

_INVALID_FILENAME = '<>:"|?*'


@dataclass
class _DesiredLink:
    relative_path: str
    source_path: str
    reason: str | None = None


class SymlinkExportService:
    """Plans and applies a symlink projection of the library."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def plan(self, request: SymlinkTreeRequest) -> SymlinkTreePlan:
        target = _prepare_target(request.target_dir)
        scope = _scope_label(request)
        desired = await self._desired_links(request)

        supported, message = _probe_symlink(target)
        if not supported:
            return SymlinkTreePlan(
                target_dir=target,
                scope=scope,
                supported=False,
                message=message,
                manifest_path=_manifest_path(target),
            )

        manifest = _read_manifest(target)
        actions = _plan_actions(target, desired, manifest)
        counts = _counts(actions)
        return SymlinkTreePlan(
            target_dir=target,
            scope=scope,
            **counts,
            actions=actions[:_ACTION_SAMPLE],
            manifest_path=_manifest_path(target),
        )

    async def apply(self, request: SymlinkTreeRequest) -> SymlinkTreeResult:
        target = _prepare_target(request.target_dir)
        scope = _scope_label(request)
        desired = await self._desired_links(request)

        supported, message = _probe_symlink(target)
        if not supported:
            raise ValueError(message or "Symlinks are not supported here")

        manifest = _read_manifest(target)
        actions = _plan_actions(target, desired, manifest)

        created = updated = removed = kept = conflicts = 0
        errors: list[str] = []
        new_links: dict[str, str] = {}

        for action in actions:
            absolute = _abs(target, action.relative_path)
            try:
                if action.action == "create":
                    os.makedirs(os.path.dirname(absolute), exist_ok=True)
                    os.symlink(action.source_path or "", absolute)
                    new_links[action.relative_path] = action.source_path or ""
                    created += 1
                elif action.action == "update":
                    if os.path.islink(absolute):
                        os.unlink(absolute)
                    os.makedirs(os.path.dirname(absolute), exist_ok=True)
                    os.symlink(action.source_path or "", absolute)
                    new_links[action.relative_path] = action.source_path or ""
                    updated += 1
                elif action.action == "remove":
                    if os.path.islink(absolute):
                        os.unlink(absolute)
                    removed += 1
                elif action.action == "keep":
                    new_links[action.relative_path] = action.source_path or ""
                    kept += 1
                else:
                    conflicts += 1
            except OSError as exc:
                errors.append(f"{action.relative_path}: {exc}")

        _write_manifest(target, scope, new_links)
        return SymlinkTreeResult(
            target_dir=target,
            scope=scope,
            created=created,
            updated=updated,
            removed=removed,
            kept=kept,
            conflicts=conflicts,
            errors=errors[:20],
            manifest_path=_manifest_path(target),
        )

    # ------------------------------------------------------------------
    # Desired links
    # ------------------------------------------------------------------

    async def _desired_links(
        self, request: SymlinkTreeRequest
    ) -> list[_DesiredLink]:
        if request.scope == "view":
            return await self._view_links(request.view_id)
        return await self._library_links()

    async def _library_links(self) -> list[_DesiredLink]:
        categories = await CategoryRepository.list_all(self._session)
        by_id = {category.id: category for category in categories}
        directories = {
            category.id: _concept_rel_dir(category, by_id)
            for category in categories
            if category.status != "merged"
        }

        assignment_rows = (
            await self._session.execute(
                select(
                    DocumentCategory.document_id,
                    DocumentCategory.category_id,
                )
            )
        ).all()
        assigned_documents = {document_id for document_id, _ in assignment_rows}
        representatives = await self._representative_files()

        desired: list[_DesiredLink] = []
        used: set[str] = set()

        for document_id, category_id in assignment_rows:
            file = representatives.get(document_id)
            directory = directories.get(category_id)
            if file is None or directory is None or not os.path.exists(file.path):
                continue
            relative = _unique(
                used, f"{directory}/{_safe_name(file.filename)}"
            )
            desired.append(_DesiredLink(relative, file.path))

        for document_id, file in representatives.items():
            if document_id is not None and document_id in assigned_documents:
                continue
            if not os.path.exists(file.path):
                continue
            relative = _unique(
                used, f"_uncategorised/{_safe_name(file.filename)}"
            )
            desired.append(_DesiredLink(relative, file.path))

        return desired

    async def _view_links(self, view_id: int | None) -> list[_DesiredLink]:
        if view_id is None:
            raise ValueError("A view must be selected for a view export")
        view = await ViewRepository.get(self._session, view_id)
        if view is None:
            raise ValueError(f"View {view_id} not found")

        query = ViewQuery.model_validate(view.query)
        service = ViewQueryService(self._session)
        files: list[File] = []
        offset = 0
        while offset < _MAX_VIEW_FILES:
            page = await service.page(query, offset=offset, limit=_PAGE_SIZE)
            files.extend(page.files)
            offset += _PAGE_SIZE
            if offset >= page.total or not page.files:
                break

        directory = make_slug(view.name) or f"view-{view.id}"
        used: set[str] = set()
        desired: list[_DesiredLink] = []
        for file in files:
            if not os.path.exists(file.path):
                continue
            relative = _unique(
                used, f"{directory}/{_safe_name(file.filename)}"
            )
            desired.append(_DesiredLink(relative, file.path))
        return desired

    async def _representative_files(self) -> dict[int | None, File]:
        """Newest file per document (plus unlinked files keyed to None)."""
        rows = (
            await self._session.execute(
                select(File).order_by(
                    File.file_modified_at.desc(), File.id.desc()
                )
            )
        ).scalars().all()
        representatives: dict[int | None, File] = {}
        for file in rows:
            key = file.document_id
            if key not in representatives:
                representatives[key] = file
        return representatives


# --------------------------------------------------------------------------
# Filesystem helpers
# --------------------------------------------------------------------------


def _prepare_target(raw: str) -> str:
    target = os.path.abspath(os.path.expanduser(raw.strip()))
    if os.path.exists(target) and not os.path.isdir(target):
        raise ValueError("Export target must be a directory")
    os.makedirs(target, exist_ok=True)
    return target


def _scope_label(request: SymlinkTreeRequest) -> str:
    if request.scope == "view":
        return f"view:{request.view_id}"
    return "library"


def _probe_symlink(target: str) -> tuple[bool, str | None]:
    probe_source = os.path.join(target, ".cabinet-probe-source")
    probe_link = os.path.join(target, ".cabinet-probe-link")
    for path in (probe_link, probe_source):
        with contextlib.suppress(OSError):
            os.unlink(path)
    try:
        with open(probe_source, "w", encoding="utf-8") as handle:
            handle.write("probe")
        os.symlink(probe_source, probe_link)
        return True, None
    except OSError as exc:
        hint = (
            " On Windows, enable Developer Mode or run as administrator; "
            "the .lnk fallback is not wired yet."
            if sys.platform == "win32"
            else ""
        )
        return (
            False,
            f"Symlinks are not available in this folder ({exc}).{hint}",
        )
    finally:
        for path in (probe_link, probe_source):
            with contextlib.suppress(OSError):
                os.unlink(path)


def _manifest_path(target: str) -> str:
    return os.path.join(target, MANIFEST_NAME)


def _read_manifest(target: str) -> dict[str, str]:
    path = _manifest_path(target)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read export manifest: {exc}") from exc
    if int(data.get("version", 0)) != MANIFEST_VERSION:
        raise ValueError("Unsupported export manifest version")
    links = data.get("links")
    if not isinstance(links, dict):
        return {}
    return {str(key): str(value) for key, value in links.items()}


def _write_manifest(target: str, scope: str, links: dict[str, str]) -> None:
    path = _manifest_path(target)
    temporary = f"{path}.tmp"
    payload = {
        "version": MANIFEST_VERSION,
        "scope": scope,
        "generated_at": datetime.now(UTC).isoformat(),
        "links": dict(sorted(links.items())),
    }
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def _plan_actions(
    target: str,
    desired: list[_DesiredLink],
    manifest: dict[str, str],
) -> list[SymlinkTreeAction]:
    desired_map = {link.relative_path: link for link in desired}
    actions: list[SymlinkTreeAction] = []

    for relative, link in desired_map.items():
        absolute = _abs(target, relative)
        if not os.path.lexists(absolute):
            actions.append(
                SymlinkTreeAction(
                    action="create",
                    relative_path=relative,
                    source_path=link.source_path,
                )
            )
            continue
        if not os.path.islink(absolute):
            actions.append(
                SymlinkTreeAction(
                    action="conflict",
                    relative_path=relative,
                    source_path=link.source_path,
                    reason="A file or folder already exists at this path",
                )
            )
            continue
        if relative not in manifest:
            actions.append(
                SymlinkTreeAction(
                    action="conflict",
                    relative_path=relative,
                    source_path=link.source_path,
                    reason="An untracked symlink exists at this path",
                )
            )
            continue
        current = os.path.realpath(absolute)
        wanted = os.path.realpath(link.source_path)
        actions.append(
            SymlinkTreeAction(
                action="keep" if current == wanted else "update",
                relative_path=relative,
                source_path=link.source_path,
            )
        )

    for relative in manifest:
        if relative in desired_map:
            continue
        absolute = _abs(target, relative)
        if os.path.islink(absolute):
            actions.append(
                SymlinkTreeAction(action="remove", relative_path=relative)
            )
        elif os.path.lexists(absolute):
            actions.append(
                SymlinkTreeAction(
                    action="conflict",
                    relative_path=relative,
                    reason="Previously generated path is no longer a symlink",
                )
            )
    return actions


def _counts(actions: list[SymlinkTreeAction]) -> dict[str, int]:
    counts = {
        "creates": 0,
        "updates": 0,
        "removes": 0,
        "keeps": 0,
        "conflicts": 0,
    }
    mapping = {
        "create": "creates",
        "update": "updates",
        "remove": "removes",
        "keep": "keeps",
        "conflict": "conflicts",
    }
    for action in actions:
        counts[mapping[action.action]] += 1
    return counts


def _concept_rel_dir(
    category: Category, by_id: dict[int, Category]
) -> str:
    path = concept_path(category, by_id)
    parts = [make_slug(part) for part in path.split(" > ") if part.strip()]
    return "/".join(parts) or "_concept"


def _safe_name(name: str) -> str:
    cleaned = name.replace("/", "_").replace("\\", "_")
    for char in _INVALID_FILENAME:
        cleaned = cleaned.replace(char, "_")
    cleaned = cleaned.strip().strip(".")
    return cleaned or "file"


def _unique(used: set[str], relative: str) -> str:
    if relative not in used:
        used.add(relative)
        return relative
    base, extension = os.path.splitext(relative)
    index = 2
    while f"{base} ({index}){extension}" in used:
        index += 1
    result = f"{base} ({index}){extension}"
    used.add(result)
    return result


def _abs(target: str, relative: str) -> str:
    path = os.path.abspath(
        os.path.join(target, relative.replace("/", os.sep))
    )
    if os.path.commonpath([target, path]) != target:
        raise ValueError("Export path escapes the target directory")
    return path
