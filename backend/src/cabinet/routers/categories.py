"""Category management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.engine import get_session
from cabinet.db.repositories import CategoryRepository, FileRepository
from cabinet.schemas import (
    CategoryBrief,
    CategoryCreate,
    CategoryResponse,
    CategoryTreeNode,
    CategoryUpdate,
    FileListResponse,
    FileResponse,
)

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("")
async def list_categories(
    format: str = Query("flat", pattern="^(tree|flat)$"),
    session: AsyncSession = Depends(get_session),
) -> list[CategoryTreeNode] | list[CategoryResponse]:
    """Return categories in tree or flat format.

    Query parameter ``format`` accepts ``"tree"`` or ``"flat"`` (default).
    """
    if format == "tree":
        tree = await CategoryRepository.get_tree(session)
        return [CategoryTreeNode(**node) for node in tree]

    rows = await CategoryRepository.get_flat_with_counts(session)
    results: list[CategoryResponse] = []
    for row in rows:
        cat = row["category"]
        results.append(
            CategoryResponse(
                id=cat.id,
                name=cat.name,
                slug=cat.slug,
                parent_id=cat.parent_id,
                description=cat.description,
                color=cat.color,
                is_ai_suggested=cat.is_ai_suggested,
                file_count=row["file_count"],
                created_at=cat.created_at,
            )
        )
    return results


@router.post("", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category(
    body: CategoryCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create a new category."""
    if body.parent_id is not None:
        parent = await CategoryRepository.get_by_id(session, body.parent_id)
        if parent is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Parent category {body.parent_id} not found",
            )

    category = await CategoryRepository.create(
        session,
        name=body.name,
        parent_id=body.parent_id,
        color=body.color,
    )

    return CategoryResponse(
        id=category.id,
        name=category.name,
        slug=category.slug,
        parent_id=category.parent_id,
        description=category.description,
        color=category.color,
        is_ai_suggested=category.is_ai_suggested,
        file_count=0,
        created_at=category.created_at,
    )


@router.patch("/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: int,
    body: CategoryUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Update an existing category."""
    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )

    category = await CategoryRepository.update(session, category_id, **update_data)
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category {category_id} not found",
        )

    # Fetch file count for the response.
    rows = await CategoryRepository.get_flat_with_counts(session)
    file_count = 0
    for row in rows:
        if row["category"].id == category_id:
            file_count = row["file_count"]
            break

    return CategoryResponse(
        id=category.id,
        name=category.name,
        slug=category.slug,
        parent_id=category.parent_id,
        description=category.description,
        color=category.color,
        is_ai_suggested=category.is_ai_suggested,
        file_count=file_count,
        created_at=category.created_at,
    )


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Delete a category. Children are re-parented to the deleted category's parent."""
    deleted = await CategoryRepository.delete(session, category_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category {category_id} not found",
        )


@router.get("/{category_id}/files", response_model=FileListResponse)
async def get_category_files(
    category_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """Return paginated files belonging to a category."""
    category = await CategoryRepository.get_by_id(session, category_id)
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category {category_id} not found",
        )

    offset = (page - 1) * per_page
    files, total = await CategoryRepository.get_files(
        session, category_id, offset=offset, limit=per_page
    )

    # Build file responses with categories.
    file_responses: list[FileResponse] = []
    for f in files:
        detailed = await FileRepository.get_by_id(session, f.id)
        if detailed is not None:
            cats: list[CategoryBrief] = []
            for fc in detailed.categories:
                cats.append(
                    CategoryBrief(
                        id=fc.category.id,
                        name=fc.category.name,
                        slug=fc.category.slug,
                        color=fc.category.color,
                        confidence=fc.confidence,
                    )
                )
            file_responses.append(
                FileResponse(
                    id=detailed.id,
                    source_id=detailed.source_id,
                    path=detailed.path,
                    filename=detailed.filename,
                    extension=detailed.extension,
                    size_bytes=detailed.size_bytes,
                    mime_type=detailed.mime_type,
                    status=detailed.status,
                    file_modified_at=detailed.file_modified_at,
                    created_at=detailed.created_at,
                    updated_at=detailed.updated_at,
                    categories=cats,
                )
            )

    return FileListResponse(
        files=file_responses,
        total=total,
        page=page,
        per_page=per_page,
    )
