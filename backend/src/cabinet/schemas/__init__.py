from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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
    watch_enabled: bool = False
    # Tier 1 facts (Phase 2a): on for new sources, off for migrated ones.
    store_facts: bool = True
    # Tier 2 full-text index (Phase 2b): opt-in, off everywhere by default.
    store_text_index: bool = False

    _clean = field_validator("exclude_patterns")(_clean_patterns)


class SourceUpdate(BaseModel):
    label: str | None = None
    recursive: bool | None = None
    exclude_patterns: list[str] | None = None
    watch_enabled: bool | None = None
    store_facts: bool | None = None
    store_text_index: bool | None = None

    _clean = field_validator("exclude_patterns")(_clean_patterns)


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    path: str
    label: str | None
    recursive: bool
    exclude_patterns: list[str] = Field(default_factory=list)
    watch_enabled: bool = False
    # Tier 1 facts storage (Phase 2a, D3).
    store_facts: bool = False
    # Tier 2 full-text index (Phase 2b, D3): opt-in.
    store_text_index: bool = False
    # Derived at read time from the filesystem ("available" / "unavailable").
    availability: str = "available"
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
    definition: str | None = None


class CategoryUpdate(BaseModel):
    name: str | None = None
    parent_id: int | None = None
    color: str | None = None
    description: str | None = None
    definition: str | None = None


class CategoryAliasCreate(BaseModel):
    alias: str


class CategoryAliasResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    alias: str
    normalized: str
    source: str


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    parent_id: int | None
    definition: str | None = None
    # Phase 3 governance: approved | provisional | merged.
    status: str = "approved"
    origin: str = "manual"
    merged_into_id: int | None = None
    aliases: list[str] = []
    example_count: int = 0
    description: str | None
    color: str | None
    is_ai_suggested: bool
    file_count: int
    created_at: datetime


class CategoryTreeNode(CategoryResponse):
    file_count: int = 0
    children: list[CategoryTreeNode] = []


class CategoryReviewSample(BaseModel):
    file_id: int
    filename: str
    summary: str | None = None


class CategoryReviewDuplicate(BaseModel):
    category_id: int
    name: str
    path: str
    similarity: float


class CategoryReviewItem(BaseModel):
    id: int
    name: str
    path: str
    definition: str | None = None
    origin: str = "ai"
    document_count: int = 0
    created_at: datetime
    samples: list[CategoryReviewSample] = []
    possible_duplicates: list[CategoryReviewDuplicate] = []


class CategoryApprove(BaseModel):
    definition: str | None = None


class CategoryMergeRequest(BaseModel):
    target_id: int


class CategoryMergeResponse(BaseModel):
    source_id: int
    target_id: int
    assignments_moved: int = 0
    aliases_added: int = 0
    conflicts: list[str] = []


class ConsolidationMergeProposal(BaseModel):
    source_id: int
    source_name: str
    target_id: int
    target_name: str
    similarity: float | None = None
    reason: str | None = None
    origin: str = "deterministic"


class ConsolidationRenameProposal(BaseModel):
    category_id: int
    current_name: str
    proposed_name: str
    reason: str | None = None
    origin: str = "llm"


class ConsolidationResponse(BaseModel):
    merges: list[ConsolidationMergeProposal] = []
    renames: list[ConsolidationRenameProposal] = []


class TaxonomyProposalNode(BaseModel):
    name: str
    definition: str | None = None
    children: list[TaxonomyProposalNode] = []


class TaxonomyProposalResponse(BaseModel):
    categories: list[TaxonomyProposalNode] = []
    sampled_documents: int = 0


class TaxonomyProposalApplyRequest(BaseModel):
    categories: list[TaxonomyProposalNode]


class TaxonomyProposalApplyResponse(BaseModel):
    created: int = 0
    reused: int = 0


class TaxonomyInsights(BaseModel):
    total_documents: int = 0
    total_concepts: int = 0
    approved_concepts: int = 0
    provisional_concepts: int = 0
    merged_concepts: int = 0
    singleton_rate: float = 0.0
    concepts_per_100_documents: float = 0.0
    possible_duplicate_pairs: int = 0
    aliases_total: int = 0
    concepts_with_aliases: int = 0
    confidence_buckets: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Views (Phase 4a): saved live projections over facts + concepts
# ---------------------------------------------------------------------------


class ViewConceptFilter(BaseModel):
    ids: list[int] = Field(default_factory=list)
    include_descendants: bool = True
    match: Literal["any", "all"] = "any"


class ViewQuery(BaseModel):
    concepts: ViewConceptFilter | None = None
    doc_types: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    sources: list[int] = Field(default_factory=list)
    text: str | None = None
    clusters: Literal["collapse", "separate"] = "collapse"
    # Version-cluster views (Phase 4c): filter to clustered / singleton docs.
    versions: Literal["all", "clustered", "singletons"] = "all"
    group_by: Literal["none", "concept", "doc_type", "topic", "source", "month"] = (
        "none"
    )
    sort_by: str = "file_modified_at"
    sort_dir: Literal["asc", "desc"] = "desc"


class ViewCreate(BaseModel):
    name: str
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    query: ViewQuery = Field(default_factory=ViewQuery)
    pinned: bool = False


class ViewUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    query: ViewQuery | None = None
    pinned: bool | None = None


class ViewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    icon: str | None
    color: str | None
    query: ViewQuery
    pinned: bool
    created_at: datetime
    updated_at: datetime | None = None


class ViewPreviewRequest(BaseModel):
    query: ViewQuery


class ViewFacetValue(BaseModel):
    value: str
    count: int


class ViewSourceFacet(BaseModel):
    source_id: int
    label: str
    count: int


class ViewFacets(BaseModel):
    total: int = 0
    doc_types: list[ViewFacetValue] = []
    topics: list[ViewFacetValue] = []
    sources: list[ViewSourceFacet] = []
    months: list[ViewFacetValue] = []


class ViewGroup(BaseModel):
    key: str
    label: str
    count: int


class ViewGroups(BaseModel):
    group_by: str = "none"
    total: int = 0
    groups: list[ViewGroup] = []


# ---------------------------------------------------------------------------
# File schemas
# ---------------------------------------------------------------------------

class FileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    # Content identity (Phase 1a): instances sharing a document_id are the
    # same document at different locations.
    document_id: int | None = None
    path: str
    filename: str
    extension: str | None
    size_bytes: int
    mime_type: str | None
    status: str
    # "available" | "source_unavailable" | "cloud_placeholder" | "missing"
    # (derived at read time, design §4.4).
    availability: str = "available"
    # Near-duplicate/version cluster (Phase 2b, design §3.4); None = singleton.
    cluster_id: int | None = None
    # Populated by view queries: documents in this cluster (1 = singleton).
    version_count: int = 1
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


class FileFactsResponse(BaseModel):
    """Derived Tier-1 facts for a file's document (Phase 2a)."""

    model_config = ConfigDict(from_attributes=True)

    content_hash: str
    extractor_version: str
    facts_version: int
    summary: str | None
    doc_type: str | None
    language: str | None
    entities: dict[str, Any] | None = None
    topics: list[str] | None = None
    provenance: dict[str, Any] | None = None
    char_count: int | None = None
    page_count: int | None = None
    # True when extractor/facts versions no longer match (will be rebuilt).
    stale: bool = False
    updated_at: datetime | None = None


class FileVersionBrief(BaseModel):
    document_id: int
    file_id: int
    filename: str
    path: str
    file_modified_at: datetime | None
    is_primary: bool = False


class FileVersionList(BaseModel):
    cluster_id: int | None = None
    versions: list[FileVersionBrief] = []


class SimilarDocument(BaseModel):
    document_id: int | None = None
    file_id: int | None = None
    filename: str | None = None
    path: str | None = None
    summary: str | None = None
    doc_type: str | None = None
    topics: list[str] | None = None
    similarity: float = 0.0


class SimilarDocuments(BaseModel):
    source_document_id: int | None = None
    items: list[SimilarDocument] = []


# ---------------------------------------------------------------------------
# Job schemas
# ---------------------------------------------------------------------------

class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_type: str
    trigger: str = "manual"
    status: str
    source_id: int | None
    total_files: int
    processed_files: int
    moved_count: int = 0
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


class EmbeddingConfigCreate(BaseModel):
    """Separate embedding config (Anthropic chat has no embeddings API)."""

    provider: str = "ollama"
    model: str = "nomic-embed-text"
    api_key: str | None = None
    base_url: str | None = "http://localhost:11434/v1"


class LLMConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str = "chat"
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
