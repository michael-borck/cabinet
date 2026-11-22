"""File browsing and category-assignment endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.engine import get_session
from cabinet.db.models import Document, DocumentCategory
from cabinet.db.repositories import (
    CategoryRepository,
    ContentFactRepository,
    DocumentRepository,
    FileRepository,
    SourceRepository,
    effective_categories,
)
from cabinet.extractors.registry import EXTRACTOR_VERSION
from cabinet.schemas import (
    CategoryBrief,
    FileCategoryAssign,
    FileFactsResponse,
    FileListResponse,
    FileResponse,
    FileVersionBrief,
    FileVersionList,
    SimilarDocuments,
)
from cabinet.services.facts import FACTS_VERSION
from cabinet.services.similar import SimilarDocumentsService

router = APIRouter(prefix="/files", tags=["files"])


def _build_file_response(file, *, source_available: bool = True) -> FileResponse:
    """Convert a File ORM instance into a FileResponse schema.

    ``categories`` is the merged effective set: document-level assignments
    plus any per-instance additions (see ``effective_categories``).
    ``availability`` is derived at read time (design §4.4).
    """
    availability = file.availability_state(source_available=source_available)
    cats: list[CategoryBrief] = [
        CategoryBrief(
            id=category.id,
            name=category.name,
            slug=category.slug,
            color=category.color,
            confidence=confidence,
        )
        for category, confidence, _is_manual in effective_categories(file)
    ]

    return FileResponse(
        id=file.id,
        source_id=file.source_id,
        document_id=file.document_id,
        path=file.path,
        filename=file.filename,
        extension=file.extension,
        size_bytes=file.size_bytes,
        mime_type=file.mime_type,
        status=file.status,
        availability=availability,
        cluster_id=(
            file.document.cluster_id if file.document is not None else None
        ),
        file_modified_at=file.file_modified_at,
        created_at=file.created_at,
        updated_at=file.updated_at,
        categories=cats,
    )


@router.get("", response_model=FileListResponse)
async def list_files(
    category_id: int | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    extension: str | None = Query(None),
    source_id: int | None = Query(None),
    q: str | None = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    sort_by: str = Query("created_at"),
    session: AsyncSession = Depends(get_session),
):
    """List files with optional filtering, text search, and pagination."""
    offset = (page - 1) * per_page
    files, total = await FileRepository.list_files(
        session,
        category_id=category_id,
        status=status_filter,
        extension=extension,
        source_id=source_id,
        query=q,
        sort_by=sort_by,
        offset=offset,
        limit=per_page,
    )

    # Categories are eager-loaded by list_files; no per-file queries needed.
    # Source availability is one stat per source, cached for this request.
    sources = await SourceRepository.list_all(session)
    available = {s.id: s.availability == "available" for s in sources}
    file_responses = [
        _build_file_response(f, source_available=available.get(f.source_id, True))
        for f in files
    ]

    return FileListResponse(
        files=file_responses,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/{file_id}", response_model=FileResponse)
async def get_file(
    file_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Return detailed information for a single file."""
    file = await FileRepository.get_by_id(session, file_id)
    if file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File {file_id} not found",
        )
    source = await SourceRepository.get_by_id(session, file.source_id)
    return _build_file_response(
        file,
        source_available=source is None or source.availability == "available",
    )


@router.get("/{file_id}/facts", response_model=FileFactsResponse)
async def get_file_facts(
    file_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Return cached Tier-1 facts for this file's document, if any."""
    file = await FileRepository.get_by_id(session, file_id)
    if file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File {file_id} not found",
        )
    content_hash = file.content_hash
    if content_hash is None and file.document_id is not None:
        document = await session.get(Document, file.document_id)
        content_hash = document.content_hash if document is not None else None
    if content_hash is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No facts: file has no content hash.",
        )
    fact = await ContentFactRepository.get(session, content_hash)
    if fact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No facts stored for this document.",
        )
    response = FileFactsResponse.model_validate(fact)
    response.stale = not ContentFactRepository.is_fresh(
        fact,
        extractor_version=EXTRACTOR_VERSION,
        facts_version=FACTS_VERSION,
    )
    return response


@router.get("/{file_id}/versions", response_model=FileVersionList)
async def get_file_versions(
    file_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Return sibling versions grouped with this file's document, if any.

    Grouping is by content similarity + filename stem + folder, never a
    merge: every version remains its own document (design §3.4).
    """
    file = await FileRepository.get_by_id(session, file_id)
    if file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File {file_id} not found",
        )
    cluster_id = file.document.cluster_id if file.document is not None else None
    if cluster_id is None:
        return FileVersionList(
            cluster_id=None,
            versions=[
                FileVersionBrief(
                    document_id=file.document_id or 0,
                    file_id=file.id,
                    filename=file.filename,
                    path=file.path,
                    file_modified_at=file.file_modified_at,
                    is_primary=True,
                )
            ],
        )

    pairs = await DocumentRepository.list_cluster(session, cluster_id)
    versions = [
        FileVersionBrief(
            document_id=document.id,
            file_id=instance.id,
            filename=instance.filename,
            path=instance.path,
            file_modified_at=instance.file_modified_at,
            is_primary=(index == 0),  # newest by mtime
        )
        for index, (document, instance) in enumerate(pairs)
    ]
    return FileVersionList(cluster_id=cluster_id, versions=versions)


@router.get("/{file_id}/similar", response_model=SimilarDocuments)
async def get_similar_files(
    file_id: int,
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
):
    """Nearest documents by Phase 2b embedding cosine (Phase 4c)."""
    result = await SimilarDocumentsService(session).for_file(file_id, limit=limit)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File {file_id} not found",
        )
    return result


@router.post(
    "/{file_id}/categories",
    response_model=FileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def assign_categories(
    file_id: int,
    body: FileCategoryAssign,
    session: AsyncSession = Depends(get_session),
):
    """Manually assign categories to a file."""
    file = await FileRepository.get_by_id(session, file_id)
    if file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File {file_id} not found",
        )

    # Validate that all requested categories exist.
    for cat_id in body.category_ids:
        category = await CategoryRepository.get_by_id(session, cat_id)
        if category is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Category {cat_id} not found",
            )

    # Manual assignment is at document level (design §3.1): every instance
    # of this document shares it. Per-instance additions remain possible
    # via FileCategory rows created elsewhere, but the API's "assign to
    # this file" means "file this document".
    doc = await DocumentRepository.ensure_for_file(session, file)
    doc_rows = (
        await session.execute(
            select(DocumentCategory).where(DocumentCategory.document_id == doc.id)
        )
    ).scalars().all()
    existing_doc = {dc.category_id: dc for dc in doc_rows}
    existing_inst = {fc.category_id: fc for fc in file.categories}

    for cat_id in body.category_ids:
        dc = existing_doc.get(cat_id)
        if dc is not None:
            # Re-asserting an AI assignment pins it: only is_manual rows
            # survive recategorization, so a plain skip would let the next
            # run silently drop a category the user explicitly confirmed.
            dc.is_manual = True
        elif cat_id in existing_inst:
            existing_inst[cat_id].is_manual = True
        else:
            session.add(
                DocumentCategory(
                    document_id=doc.id,
                    category_id=cat_id,
                    is_manual=True,
                )
            )

    await session.flush()

    # Phase 3b learning loop: a manual assignment is a positive example.
    content_hash = file.content_hash or doc.content_hash
    summary = None
    if content_hash:
        fact = await ContentFactRepository.get(session, content_hash)
        summary = fact.summary if fact is not None else None
    for cat_id in body.category_ids:
        await CategoryRepository.add_example(
            session,
            cat_id,
            filename=file.filename,
            content_hash=content_hash,
            summary_snippet=summary[:300] if summary else None,
            source="manual",
        )

    # Relationship collections are stale after raw FK inserts; expire them
    # so the re-fetch reloads the merged effective set.
    session.expire(file, ["categories", "document"])
    session.expire(doc, ["categories"])
    file = await FileRepository.get_by_id(session, file_id)
    return _build_file_response(file)


@router.delete(
    "/{file_id}/categories/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_category(
    file_id: int,
    category_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Remove a category assignment from a file."""
    file = await FileRepository.get_by_id(session, file_id)
    if file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File {file_id} not found",
        )

    # Remove at whichever level(s) the assignment lives, so the category
    # actually disappears from the effective set.
    removed = False
    ai_confident = False
    if file.document is not None:
        for dc in list(file.document.categories):
            if dc.category_id == category_id:
                if not dc.is_manual and (dc.confidence or 0.0) >= 0.8:
                    ai_confident = True
                await session.delete(dc)
                removed = True
    for fc in list(file.categories):
        if fc.category_id == category_id:
            if not fc.is_manual and (fc.confidence or 0.0) >= 0.8:
                ai_confident = True
            await session.delete(fc)
            removed = True

    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File {file_id} does not have category {category_id}",
        )

    # Learning loop: a confidently AI-assigned category the user removed is
    # a negative example (used by later consolidation/learning passes).
    if ai_confident:
        content_hash = file.content_hash or (
            file.document.content_hash if file.document is not None else None
        )
        summary = None
        if content_hash:
            fact = await ContentFactRepository.get(session, content_hash)
            summary = fact.summary if fact is not None else None
        await CategoryRepository.add_example(
            session,
            category_id,
            filename=file.filename,
            content_hash=content_hash,
            summary_snippet=summary[:300] if summary else None,
            source="manual_negative",
        )

    await session.flush()
