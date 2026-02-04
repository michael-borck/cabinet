"""File browsing and category-assignment endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.engine import get_session
from cabinet.db.models import FileCategory
from cabinet.db.repositories import CategoryRepository, FileRepository
from cabinet.schemas import (
    CategoryBrief,
    FileCategoryAssign,
    FileListResponse,
    FileResponse,
)

router = APIRouter(prefix="/files", tags=["files"])


def _build_file_response(file) -> FileResponse:
    """Convert a File ORM instance into a FileResponse schema.

    Builds the ``categories`` list from the eager-loaded
    ``file.categories`` relationship (which points to FileCategory rows).
    """
    cats: list[CategoryBrief] = []
    for fc in file.categories:
        cats.append(
            CategoryBrief(
                id=fc.category.id,
                name=fc.category.name,
                slug=fc.category.slug,
                color=fc.category.color,
                confidence=fc.confidence,
            )
        )

    return FileResponse(
        id=file.id,
        source_id=file.source_id,
        path=file.path,
        filename=file.filename,
        extension=file.extension,
        size_bytes=file.size_bytes,
        mime_type=file.mime_type,
        status=file.status,
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
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    sort_by: str = Query("created_at"),
    session: AsyncSession = Depends(get_session),
):
    """List files with optional filtering and pagination."""
    offset = (page - 1) * per_page
    files, total = await FileRepository.list_files(
        session,
        category_id=category_id,
        status=status_filter,
        extension=extension,
        source_id=source_id,
        sort_by=sort_by,
        offset=offset,
        limit=per_page,
    )

    # Eagerly load categories for each file in the response.
    file_responses: list[FileResponse] = []
    for f in files:
        # list_files doesn't eager-load categories, so fetch individually.
        detailed = await FileRepository.get_by_id(session, f.id)
        if detailed is not None:
            file_responses.append(_build_file_response(detailed))

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
    return _build_file_response(file)


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

    # Build existing category id set to avoid duplicates.
    existing_cat_ids = {fc.category_id for fc in file.categories}

    for cat_id in body.category_ids:
        if cat_id not in existing_cat_ids:
            fc = FileCategory(
                file_id=file_id,
                category_id=cat_id,
                is_manual=True,
            )
            session.add(fc)

    await session.flush()

    # Re-fetch file with updated categories.
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

    target_fc = None
    for fc in file.categories:
        if fc.category_id == category_id:
            target_fc = fc
            break

    if target_fc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File {file_id} does not have category {category_id}",
        )

    await session.delete(target_fc)
    await session.flush()
