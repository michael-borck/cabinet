from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

class PaginationParams(BaseModel):
    page: int = 1
    per_page: int = 50


# ---------------------------------------------------------------------------
# Source schemas
# ---------------------------------------------------------------------------

def _clean_patterns(value: list[str] | None) -> list[str] | None:
    """Strip whitespace and drop empty entries; preserve None (field omitted)."""
    if value is None:
        return None
    return [p.strip() for p in value if p.strip()]


class SourceCreate(BaseModel):
    path: str
    label: str | None = None
    recursive: bool = True
    exclude_patterns: list[str] = Field(default_factory=list)

    _clean = field_validator("exclude_patterns")(_clean_patterns)


class SourceUpdate(BaseModel):
    label: str | None = None
    recursive: bool | None = None
    exclude_patterns: list[str] | None = None

    _clean = field_validator("exclude_patterns")(_clean_patterns)


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    path: str
    label: str | None
    recursive: bool
    exclude_patterns: list[str] = Field(default_factory=list)
    last_scanned_at: datetime | None
    created_at: datetime

    @field_validator("exclude_patterns", mode="before")
    @classmethod
    def _none_to_empty(cls, value: list[str] | None) -> list[str]:
        return value or []


class ScanRequest(BaseModel):
    mode: Literal["quick_sync", "full_reindex"] = "quick_sync"


# ---------------------------------------------------------------------------
# Category schemas (defined before File schemas because FileResponse uses
# CategoryBrief)
# ---------------------------------------------------------------------------

class CategoryBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    color: str | None
    confidence: float | None = None


class CategoryCreate(BaseModel):
    name: str
    parent_id: int | None = None
    color: str | None = None


class CategoryUpdate(BaseModel):
    name: str | None = None
    parent_id: int | None = None
    color: str | None = None
    description: str | None = None


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    parent_id: int | None
    description: str | None
    color: str | None
    is_ai_suggested: bool
    file_count: int
    created_at: datetime


class CategoryTreeNode(CategoryResponse):
    file_count: int = 0
    children: list[CategoryTreeNode] = []


# ---------------------------------------------------------------------------
# File schemas
# ---------------------------------------------------------------------------

class FileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    path: str
    filename: str
    extension: str | None
    size_bytes: int
    mime_type: str | None
    status: str
    file_modified_at: datetime | None
    created_at: datetime
    updated_at: datetime
    categories: list[CategoryBrief] = []


class FileListResponse(BaseModel):
    files: list[FileResponse]
    total: int
    page: int
    per_page: int


class FileCategoryAssign(BaseModel):
    category_ids: list[int]


# ---------------------------------------------------------------------------
# Job schemas
# ---------------------------------------------------------------------------

class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_type: str
    status: str
    source_id: int | None
    total_files: int
    processed_files: int
    error_count: int
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def progress_pct(self) -> float:
        if self.total_files == 0:
            return 0.0
        return round((self.processed_files / self.total_files) * 100, 1)


class JobEvent(BaseModel):
    job_id: int
    status: str
    processed_files: int
    total_files: int
    message: str | None = None


# ---------------------------------------------------------------------------
# LLM / Settings schemas
# ---------------------------------------------------------------------------

class LLMConfigCreate(BaseModel):
    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None


class LLMConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider: str
    model: str
    base_url: str | None
    is_active: bool
    # Never serialized -- clients only see the derived ``api_key_set`` flag.
    api_key: str | None = Field(default=None, exclude=True)
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def api_key_set(self) -> bool:
        return self.api_key is not None


class LLMTestResult(BaseModel):
    success: bool
    message: str
