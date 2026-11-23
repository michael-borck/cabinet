"""Obsidian vault export (Phase 5d, design §6).

One markdown stub per document: facts as frontmatter, concepts as frontmatter
tags, and a link to the original. Notes are written under ``<target>/Cabinet``
and never overwrite an existing file (create-only, so user edits are safe).
"""

from __future__ import annotations

import json
import logging
import os
import pathlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import (
    ContentFact,
    DocumentCategory,
    File,
    FileCategory,
)
from cabinet.db.repositories import (
    CategoryRepository,
    SourceRepository,
    ViewRepository,
)
from cabinet.schemas import (
    VaultExportPlan,
    VaultExportRequest,
    VaultExportResult,
    ViewQuery,
)
from cabinet.services.concepts import concept_path
from cabinet.services.views import ViewQueryService
from cabinet.utils.text import make_slug

logger = logging.getLogger(__name__)

NOTE_FOLDER = "Cabinet"
_MAX_NOTE_STEM = 100
_PAGE_SIZE = 500
_MAX_VIEW_FILES = 5000
_INVALID_FILENAME = '<>:"|?*'


class VaultExportService:
    """Plans and writes Obsidian catalogue cards."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def plan(self, request: VaultExportRequest) -> VaultExportPlan:
        target = _prepare_target(request.target_dir)
        notes = await self._notes(request)
        creates = conflicts = 0
        samples: list[str] = []
        folder = os.path.join(target, NOTE_FOLDER)
        for filename, _content in notes:
            path = os.path.join(folder, filename)
            if os.path.exists(path):
                conflicts += 1
            else:
                creates += 1
                if len(samples) < 20:
                    samples.append(filename)
        return VaultExportPlan(
            target_dir=target,
            scope=_scope_label(request),
            notes=len(notes),
            creates=creates,
            conflicts=conflicts,
            samples=samples,
        )

    async def apply(self, request: VaultExportRequest) -> VaultExportResult:
        target = _prepare_target(request.target_dir)
        folder = os.path.join(target, NOTE_FOLDER)
        os.makedirs(folder, exist_ok=True)

        notes = await self._notes(request)
        created = conflicts = 0
        errors: list[str] = []
        for filename, content in notes:
            path = os.path.join(folder, filename)
            if os.path.exists(path):
                conflicts += 1
                continue
            try:
                with open(path, "x", encoding="utf-8") as handle:
                    handle.write(content)
                created += 1
            except OSError as exc:
                errors.append(f"{filename}: {exc}")
        return VaultExportResult(
            target_dir=target,
            scope=_scope_label(request),
            created=created,
            conflicts=conflicts,
            errors=errors[:20],
        )

    # ------------------------------------------------------------------
    # Note construction
    # ------------------------------------------------------------------

    async def _notes(
        self, request: VaultExportRequest
    ) -> list[tuple[str, str]]:
        categories = await CategoryRepository.list_all(self._session)
        by_id = {category.id: category for category in categories}
        concept_paths = {
            category.id: concept_path(category, by_id)
            for category in categories
            if category.status != "merged"
        }
        concept_tags = {
            category_id: "/".join(
                make_slug(part)
                for part in path.split(" > ")
                if part.strip()
            )
            for category_id, path in concept_paths.items()
        }

        document_categories = _group_pairs(
            (
                await self._session.execute(
                    select(
                        DocumentCategory.document_id,
                        DocumentCategory.category_id,
                    )
                )
            ).all()
        )
        instance_categories = _group_pairs(
            (
                await self._session.execute(
                    select(FileCategory.file_id, FileCategory.category_id)
                )
            ).all()
        )
        sources = {
            source.id: source.label or source.path
            for source in await SourceRepository.list_all(self._session)
        }

        representatives = await self._representatives(request)
        hashes = [
            file.content_hash
            for file in representatives.values()
            if file.content_hash
        ]
        facts = {}
        if hashes:
            rows = (
                await self._session.execute(
                    select(ContentFact).where(
                        ContentFact.content_hash.in_(hashes)
                    )
                )
            ).scalars().all()
            facts = {fact.content_hash: fact for fact in rows}

        used: set[str] = set()
        notes: list[tuple[str, str]] = []
        for document_id, file in representatives.items():
            filename = _note_filename(file, used)
            concept_ids = set(document_categories.get(document_id, ()))
            concept_ids.update(instance_categories.get(file.id, ()))
            paths = sorted(
                concept_paths[category_id]
                for category_id in concept_ids
                if category_id in concept_paths
            )
            tags = sorted(
                concept_tags[category_id]
                for category_id in concept_ids
                if category_id in concept_tags
            )
            content = _note_content(
                file,
                facts.get(file.content_hash or ""),
                paths,
                tags,
                sources.get(file.source_id, ""),
            )
            notes.append((filename, content))
        notes.sort(key=lambda note: note[0].lower())
        return notes

    async def _representatives(
        self, request: VaultExportRequest
    ) -> dict[int, File]:
        if request.scope == "view":
            if request.view_id is None:
                raise ValueError("A view must be selected for a view export")
            view = await ViewRepository.get(self._session, request.view_id)
            if view is None:
                raise ValueError(f"View {request.view_id} not found")
            query = ViewQuery.model_validate(view.query)
            service = ViewQueryService(self._session)
            files: list[File] = []
            offset = 0
            while offset < _MAX_VIEW_FILES:
                page = await service.page(
                    query, offset=offset, limit=_PAGE_SIZE
                )
                files.extend(page.files)
                offset += _PAGE_SIZE
                if offset >= page.total or not page.files:
                    break
            representatives: dict[int, File] = {}
            for file in files:
                if file.document_id is not None:
                    representatives.setdefault(file.document_id, file)
            return representatives

        rows = (
            await self._session.execute(
                select(File)
                .where(File.document_id.isnot(None))
                .order_by(File.file_modified_at.desc(), File.id.desc())
            )
        ).scalars().all()
        representatives: dict[int, File] = {}
        for file in rows:
            representatives.setdefault(file.document_id, file)
        return representatives


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _prepare_target(raw: str) -> str:
    target = os.path.abspath(os.path.expanduser(raw.strip()))
    if os.path.exists(target) and not os.path.isdir(target):
        raise ValueError("Vault target must be a directory")
    os.makedirs(target, exist_ok=True)
    return target


def _scope_label(request: VaultExportRequest) -> str:
    return f"view:{request.view_id}" if request.scope == "view" else "library"


def _group_pairs(rows) -> dict[int, set[int]]:
    grouped: dict[int, set[int]] = {}
    for key, category_id in rows:
        if key is None:
            continue
        grouped.setdefault(key, set()).add(category_id)
    return grouped


def _note_filename(file: File, used: set[str]) -> str:
    stem = os.path.splitext(file.filename)[0]
    cleaned = stem.replace("/", "_").replace("\\", "_")
    for char in _INVALID_FILENAME:
        cleaned = cleaned.replace(char, "_")
    cleaned = (cleaned.strip().strip(".") or "document")[:_MAX_NOTE_STEM]
    candidate = f"{cleaned}.md"
    if candidate in used:
        digest = (file.content_hash or "").split(":", 1)[-1][:8] or "copy"
        candidate = f"{cleaned}-{digest}.md"
    index = 2
    while candidate in used:
        candidate = f"{cleaned}-{index}.md"
        index += 1
    used.add(candidate)
    return candidate


def _note_content(
    file: File,
    fact: ContentFact | None,
    concepts: list[str],
    tags: list[str],
    source: str,
) -> str:
    original = file.path
    try:
        uri = pathlib.Path(original).as_uri()
    except ValueError:
        uri = original

    lines = ["---"]
    lines.append(f"cabinet_hash: {json.dumps(file.content_hash or '')}")
    lines.append(f"filename: {json.dumps(file.filename)}")
    lines.append(f"path: {json.dumps(original)}")
    lines.append(f"source: {json.dumps(source)}")
    if fact is not None:
        if fact.doc_type:
            lines.append(f"doc_type: {json.dumps(fact.doc_type)}")
        if fact.language:
            lines.append(f"language: {json.dumps(fact.language)}")
        if fact.topics:
            lines.append(f"topics: {json.dumps(list(fact.topics))}")
        if fact.summary:
            lines.append(f"summary: {json.dumps(fact.summary)}")
    lines.append(f"concepts: {json.dumps(concepts)}")
    lines.append(f"tags: {json.dumps(tags)}")
    lines.append('generated_by: "cabinet"')
    lines.append("---")

    body = [f"# {os.path.splitext(file.filename)[0]}", ""]
    if fact is not None and fact.summary:
        body.extend([fact.summary, ""])
    body.append(f"[Open original]({uri})")
    body.append("")
    return "\n".join(lines) + "\n\n" + "\n".join(body)
