"""Portable curation sidecar (Phase 5a, design §3).

The sidecar is a JSON projection of *human* curation — concepts, aliases,
merges, manual document assignments, and saved views — keyed by content hash
where possible. It is written atomically on shutdown and by an explicit
Export; importing it onto a rebuilt, re-scanned database restores the
curation without any LLM work.

Raw text, facts, embeddings, and API keys are deliberately not included.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.config import get_settings
from cabinet.db.engine import engine
from cabinet.db.models import Category, Document, DocumentCategory
from cabinet.db.repositories import (
    CategoryRepository,
    DocumentRepository,
    ViewRepository,
)
from cabinet.services.concepts import ConceptResolver, concept_path
from cabinet.utils.concepts import normalize_concept
from cabinet.utils.text import make_slug

logger = logging.getLogger(__name__)

CURATION_VERSION = 1
_MAX_WARNINGS = 20


def default_curation_path() -> str | None:
    """Configured sidecar path, else ``curation.json`` beside the database."""
    configured = get_settings().curation_path
    if configured:
        return configured
    database = engine.url.database
    if not database or database == ":memory:":
        return None
    return os.path.join(os.path.dirname(database), "curation.json")


async def export_data(session: AsyncSession) -> dict[str, Any]:
    """Build the portable sidecar document."""
    categories = await CategoryRepository.list_all(session)
    by_id = {category.id: category for category in categories}
    paths = {
        category.id: concept_path(category, by_id) for category in categories
    }

    concepts = [
        {
            "path": paths[category.id],
            "name": category.name,
            "definition": category.definition,
            "color": category.color,
            "status": category.status,
            "aliases": [alias.alias for alias in category.aliases],
        }
        for category in categories
        if category.status != "merged"
    ]

    merges: list[dict[str, str]] = []
    for category in categories:
        if category.status != "merged" or category.merged_into_id is None:
            continue
        target = by_id.get(category.merged_into_id)
        if target is None:
            continue
        merges.append(
            {
                "source_path": paths[category.id],
                "target_path": paths[target.id],
            }
        )

    assignment_rows = (
        await session.execute(
            select(DocumentCategory, Document.content_hash)
            .join(Document, Document.id == DocumentCategory.document_id)
            .where(DocumentCategory.is_manual.is_(True))
        )
    ).all()
    assignments = [
        {
            "content_hash": content_hash,
            "concept_path": paths[document_category.category_id],
        }
        for document_category, content_hash in assignment_rows
        if content_hash and document_category.category_id in paths
    ]

    views = await ViewRepository.list_all(session)
    view_items = [
        {
            "name": view.name,
            "description": view.description,
            "color": view.color,
            "query": view.query,
            "pinned": view.pinned,
        }
        for view in views
    ]

    return {
        "version": CURATION_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "concepts": concepts,
        "merges": merges,
        "assignments": assignments,
        "views": view_items,
    }


async def import_data(
    session: AsyncSession, payload: Any
) -> dict[str, Any]:
    """Apply a sidecar document additively and idempotently."""
    if not isinstance(payload, dict):
        raise ValueError("Curation import must be a JSON object")
    try:
        version = int(payload.get("version", CURATION_VERSION))
    except (TypeError, ValueError) as exc:
        raise ValueError("Curation import has an invalid version") from exc
    if version > CURATION_VERSION:
        raise ValueError(
            f"Unsupported curation version {version} (max {CURATION_VERSION})"
        )

    resolver = ConceptResolver(session)
    counts = {
        "concepts_created": 0,
        "aliases_added": 0,
        "assignments_applied": 0,
        "assignments_skipped": 0,
        "merges_applied": 0,
        "views_created": 0,
    }
    warnings: list[str] = []

    for item in payload.get("concepts") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or item.get("name") or "").strip()
        if not path:
            continue
        existed = await _find_path(session, path) is not None
        category = await _ensure_path(session, resolver, path)
        if category is None:
            continue
        if not existed:
            counts["concepts_created"] += 1
        if item.get("definition") and not category.definition:
            category.definition = str(item["definition"])
        if item.get("color") and not category.color:
            category.color = str(item["color"])
        if category.status == "provisional" and item.get("status", "approved") == "approved":
            category.status = "approved"
        for alias in item.get("aliases") or []:
            text = str(alias).strip()
            if not text:
                continue
            if (
                await CategoryRepository.get_by_normalized_alias(
                    session, normalize_concept(text)
                )
                is not None
            ):
                continue
            try:
                await CategoryRepository.add_alias(
                    session, category.id, text, source="manual"
                )
                counts["aliases_added"] += 1
            except ValueError as exc:
                _warn(warnings, str(exc))

    for item in payload.get("merges") or []:
        if not isinstance(item, dict):
            continue
        source_path = str(item.get("source_path") or "")
        target_path = str(item.get("target_path") or "")
        source = await _find_path(session, source_path)
        target = await _find_path(session, target_path)
        if target is None:
            _warn(
                warnings,
                f"Skipped merge {source_path!r}: target not found",
            )
            continue
        if source is None:
            # A merged source is not part of `concepts`; recreate its path so
            # the merge (and its alias) can be restored on a rebuilt DB.
            source = await _ensure_path(session, resolver, source_path)
        if source is None:
            _warn(
                warnings,
                f"Skipped merge {source_path!r}: path not found",
            )
            continue
        if source.id == target.id:
            # The source name is already an alias of the target, which is the
            # merge outcome; nothing else to do.
            continue
        if source.status == "merged":
            continue
        try:
            result = await CategoryRepository.merge(
                session, source.id, target.id
            )
        except ValueError as exc:
            _warn(warnings, str(exc))
            continue
        if result is not None:
            counts["merges_applied"] += 1

    for item in payload.get("assignments") or []:
        if not isinstance(item, dict):
            continue
        content_hash = str(item.get("content_hash") or "").strip()
        concept = str(item.get("concept_path") or "").strip()
        if not content_hash or not concept:
            continue
        document = await DocumentRepository.get_by_hash(session, content_hash)
        if document is None:
            counts["assignments_skipped"] += 1
            _warn(warnings, f"Unknown content hash {content_hash}")
            continue
        category = await _ensure_path(session, resolver, concept)
        if category is None:
            continue
        existing = (
            await session.execute(
                select(DocumentCategory).where(
                    DocumentCategory.document_id == document.id,
                    DocumentCategory.category_id == category.id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if not existing.is_manual:
                existing.is_manual = True
                counts["assignments_applied"] += 1
            continue
        session.add(
            DocumentCategory(
                document_id=document.id,
                category_id=category.id,
                is_manual=True,
            )
        )
        counts["assignments_applied"] += 1

    existing_views = {
        view.name for view in await ViewRepository.list_all(session)
    }
    for item in payload.get("views") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in existing_views:
            continue
        query = item.get("query") if isinstance(item.get("query"), dict) else {}
        await ViewRepository.create(
            session,
            name=name,
            description=(
                str(item["description"]) if item.get("description") else None
            ),
            color=str(item["color"]) if item.get("color") else None,
            query=query,
            pinned=bool(item.get("pinned")),
        )
        counts["views_created"] += 1
        existing_views.add(name)

    await session.flush()
    return {**counts, "warnings": warnings}


async def write_sidecar(
    session: AsyncSession, path: str | None = None
) -> str | None:
    """Write the sidecar atomically; returns the path (or None when disabled)."""
    target = path or default_curation_path()
    if not target:
        return None
    data = await export_data(session)
    directory = os.path.dirname(os.path.abspath(target))
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = f"{target}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, target)
    return target


# --------------------------------------------------------------------------
# Path helpers
# --------------------------------------------------------------------------


async def _ensure_path(
    session: AsyncSession, resolver: ConceptResolver, path: str
) -> Category | None:
    parts = [part.strip() for part in path.split(">") if part.strip()]
    if not parts:
        return None
    parent: Category | None = None
    for part in parts:
        parent = await resolver.resolve(
            part, parent_id=parent.id if parent else None
        )
    return parent


async def _find_path(session: AsyncSession, path: str) -> Category | None:
    """Read-only canonical path lookup (no creation)."""
    parts = [part.strip() for part in path.split(">") if part.strip()]
    if not parts:
        return None
    parent_id: int | None = None
    current: Category | None = None
    for part in parts:
        query = select(Category).where(Category.slug == make_slug(part))
        if parent_id is None:
            query = query.where(Category.parent_id.is_(None))
        else:
            query = query.where(Category.parent_id == parent_id)
        current = (
            await session.execute(query.limit(1))
        ).scalar_one_or_none()
        if current is None:
            return None
        parent_id = current.id
    return current


def _warn(warnings: list[str], message: str) -> None:
    if len(warnings) < _MAX_WARNINGS:
        warnings.append(message)
