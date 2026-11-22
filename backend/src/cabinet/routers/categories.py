"""Category management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.engine import get_session
from cabinet.db.repositories import (
    CategoryRepository,
    FileRepository,
    SourceRepository,
    effective_categories,
)
from cabinet.schemas import (
    CategoryAliasCreate,
    CategoryAliasResponse,
    CategoryApprove,
    CategoryBrief,
    CategoryCreate,
    CategoryResponse,
    CategoryReviewDuplicate,
    CategoryReviewItem,
    CategoryReviewSample,
    CategoryTreeNode,
    CategoryUpdate,
    FileListResponse,
    FileResponse,
)
from cabinet.services.concepts import TaxonomyReviewService

router = APIRouter(prefix="/categories", tags=["categories"])


def _category_response(
    category,
    *,
    file_count: int = 0,
    aliases: list[str] | None = None,
    example_count: int | None = None,
) -> CategoryResponse:
    """Build a response including Phase 3 governance fields."""
    return CategoryResponse(
        id=category.id,
        name=category.name,
        slug=category.slug,
        parent_id=category.parent_id,
        definition=category.definition,
        status=category.status,
        origin=category.origin,
        merged_into_id=category.merged_into_id,
        aliases=(
            aliases
            if aliases is not None
            else [alias.alias for alias in category.aliases]
        ),
        example_count=(
            example_count
            if example_count is not None
            else len(category.examples)
        ),
        description=category.description,
        color=category.color,
        is_ai_suggested=category.is_ai_suggested,
        file_count=file_count,
        created_at=category.created_at,
    )


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
        results.append(
            _category_response(
                row["category"], file_count=row["file_count"]
            )
        )
    return results


@router.get("/review", response_model=list[CategoryReviewItem])
async def list_category_review(session: AsyncSession = Depends(get_session)):
    """Provisional concepts awaiting review, with samples and duplicates."""
    items = await TaxonomyReviewService(session).items()
    return [
        CategoryReviewItem(
            id=item.category.id,
            name=item.category.name,
            path=item.path,
            definition=item.category.definition,
            origin=item.category.origin,
            document_count=item.document_count,
            created_at=item.category.created_at,
            samples=[
                CategoryReviewSample(
                    file_id=sample.file_id,
                    filename=sample.filename,
                    summary=sample.summary,
                )
                for sample in item.samples
            ],
            possible_duplicates=[
                CategoryReviewDuplicate(
                    category_id=duplicate.category_id,
                    name=duplicate.name,
                    path=duplicate.path,
                    similarity=duplicate.similarity,
                )
                for duplicate in item.possible_duplicates
            ],
        )
        for item in items
    ]


@router.post(
    "/{category_id}/approve", response_model=CategoryResponse
)
async def approve_category(
    category_id: int,
    body: CategoryApprove | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Approve a provisional concept (optionally setting its definition)."""
    category = await TaxonomyReviewService(session).approve(
        category_id, definition=body.definition if body is not None else None
    )
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provisional category {category_id} not found",
        )
    count = await CategoryRepository.get_document_count(session, category_id)
    return _category_response(category, file_count=count)


@router.post(
    "/{category_id}/reject", status_code=status.HTTP_204_NO_CONTENT
)
async def reject_category(
    category_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Reject a provisional concept; its assignments are removed."""
    if not await TaxonomyReviewService(session).reject(category_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provisional category {category_id} not found",
        )


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
        definition=body.definition,
        origin="manual",
    )

    return _category_response(category, aliases=[], example_count=0)


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

    try:
        category = await CategoryRepository.update(session, category_id, **update_data)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category {category_id} not found",
        )

    # Fetch file count and relationship state for the response.
    rows = await CategoryRepository.get_flat_with_counts(session)
    file_count = next(
        (
            row["file_count"]
            for row in rows
            if row["category"].id == category_id
        ),
        0,
    )
    detailed = (
        await CategoryRepository.get_with_relations(session, category_id)
        or category
    )
    return _category_response(detailed, file_count=file_count)


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


@router.post(
    "/{category_id}/aliases",
    response_model=CategoryAliasResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_category_alias(
    category_id: int,
    body: CategoryAliasCreate,
    session: AsyncSession = Depends(get_session),
):
    """Attach a synonym to a concept (Phase 3)."""
    if not body.alias.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Alias must not be empty",
        )
    try:
        alias = await CategoryRepository.add_alias(
            session, category_id, body.alias, source="manual"
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    if alias is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category {category_id} not found",
        )
    return alias


@router.delete(
    "/{category_id}/aliases/{alias_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_category_alias(
    category_id: int,
    alias_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Remove a synonym from a concept."""
    if not await CategoryRepository.delete_alias(session, category_id, alias_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Alias not found for this category",
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
    all_sources = await SourceRepository.list_all(session)
    source_available = {s.id: s.availability == "available" for s in all_sources}
    file_responses: list[FileResponse] = []
    for f in files:
        detailed = await FileRepository.get_by_id(session, f.id)
        if detailed is not None:
            cats: list[CategoryBrief] = [
                CategoryBrief(
                    id=category.id,
                    name=category.name,
                    slug=category.slug,
                    color=category.color,
                    confidence=confidence,
                )
                for category, confidence, _manual in effective_categories(detailed)
            ]
            file_responses.append(
                FileResponse(
                    id=detailed.id,
                    source_id=detailed.source_id,
                    document_id=detailed.document_id,
                    path=detailed.path,
                    filename=detailed.filename,
                    extension=detailed.extension,
                    size_bytes=detailed.size_bytes,
                    mime_type=detailed.mime_type,
                    status=detailed.status,
                    availability=detailed.availability_state(
                        source_available=source_available.get(
                            detailed.source_id, True
                        )
                    ),
                    cluster_id=(
                        detailed.document.cluster_id
                        if detailed.document is not None
                        else None
                    ),
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
