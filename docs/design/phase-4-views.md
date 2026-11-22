# Design: Phase 4 — Views, not one tree

- **Status:** 4a implemented (saved live views, builder, cluster-aware
  list); 4b facets/grouping/editing and 4c semantic views pending.
- **Date:** 2026-11-23
- **Scope:** Multiple saved, live projections over the facts + concept layer:
  filter builder, grouping, facets, counts, version-cluster handling. No file
  copies and no second source of truth.
- **Related:** [Phase 1+2 design](phase-1-2-identity-watching-facts.md),
  [Phase 3 design](phase-3-taxonomy-governance.md),
  [ROADMAP](../ROADMAP.md).

## 1. The problem

Cabinet currently has one browse surface: the concept tree. That is the right
*structure*, but documents have several independent dimensions — topic,
document type, project, people, date, source — and users need different
lenses on the same pile without moving files or duplicating them in the DB.

A view is that lens: **a saved query over facts + concepts**, evaluated live.
It is not a folder, not a copy, and not a second taxonomy.

## 2. Principles

1. **Views are queries, not containers.** No rows are copied; a view is a
   named, saved filter/group/sort definition.
2. **Concepts remain the structure.** Views may filter by concept, but the
   tree is still where concepts are governed.
3. **Live by default.** Opening a view runs the query against current data;
   there are no stale snapshots to rebuild.
4. **Cheap because Phase 2 exists.** Facts and embeddings are already cached
   per document; a view is SQL, not re-extraction.
5. **Version clusters count once.** A version cluster appears as one item
   with "N versions", matching Phase 2b identity semantics.
6. **Non-destructive.** Views never move, rename, or write to source files.

## 3. Data model

### 3.1 `views`

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `name` | String | display name |
| `description` | Text, nullable | optional |
| `icon` | String, nullable | lucide icon name |
| `color` | String, nullable | accent |
| `query` | JSON | the query spec (below) |
| `pinned` | Boolean | orders the sidebar |
| `created_at`, `updated_at` | DateTime | |

No file/document rows are stored. Deleting a view deletes only the query.

### 3.2 Query spec (v1)

```json
{
  "concepts": {"ids": [12, 15], "include_descendants": true, "match": "any"},
  "doc_types": ["invoice", "contract"],
  "topics": ["billing"],
  "language": "en",
  "entities": {
    "people": [],
    "organisations": [],
    "dates": {"from": "2026-01-01", "to": "2026-12-31"},
    "amounts": {"min": 0, "max": null}
  },
  "sources": [1],
  "availability": ["available", "cloud_placeholder"],
  "text": "acme",
  "clusters": "collapse",
  "group_by": "concept",
  "sort": {"by": "file_modified_at", "dir": "desc"}
}
```

- `concepts.include_descendants` defaults to **true** (settled decision).
- `clusters`: `collapse` (default; one item per version cluster, newest as the
  representative) or `separate` (every instance). Counts always collapse.
- `group_by`: `concept` | `doc_type` | `topic` | `source` | `month` |
  `none`.
- `entities` are best-effort from Phase 2a facts; unmatched/unknown values
  simply do not match.
- Omitted/empty fields mean "no filter".

## 4. Query execution

- The existing server-side file query (`GET /files`) grows the same filters,
  so views reuse one query builder: joins to `documents`, `content_facts`,
  `document_categories`/`file_categories`, `sources`.
- **Counts** use the same WHERE clause but count *clusters*:
  `COUNT(DISTINCT COALESCE(cluster_id, -document_id))` when `collapse`.
- **Facets** are aggregate queries over the filtered set: counts by doc type,
  topic, concept, source, and month. Derived from facts; no new storage.
- **Text** continues to use the Phase 2b `q` path (facts + optional FTS).
- Sorting is allowlisted (`file_modified_at`, `filename`, `size_bytes`,
  `created_at`).
- Performance guard: filters are normal SQL over indexed columns
  (`files.source_id`, `files.status`, `documents.cluster_id`,
  `content_facts.doc_type`); topic/entity matching uses JSON extraction and
  is acceptable at personal scale. Add FTS over facts later if needed.

## 5. API

- `GET /views` — list saved views.
- `POST /views`, `PATCH /views/{id}`, `DELETE /views/{id}`.
- `GET /views/{id}/files?page=&per_page=` — paginated files.
- `GET /views/{id}/facets` — counts for the builder/list.
- `POST /views/preview` — run an unsaved query (for live builder feedback).
- Reuse `GET /files` filter params for the underlying query so the renderer
  and API stay consistent.

## 6. UI

- **Sidebar:** a "Library" section (existing tree) and a "Views" section
  (saved views, pinned first, "+ New view").
- **View builder:** concept picker (tree, include-descendants toggle), doc
  type/topic chips, source, date range, availability, text; group-by and
  sort; live preview count; Save / Update.
- **View page:** grouped list (concept/type/topic/month) or flat table;
  facets as chips with counts; "N versions" on clustered items; empty state
  suggesting filters.
- The tree browse is unchanged; views are additive.

## 7. Version clusters in views

- Collapsed mode shows one representative per cluster (newest by file mtime)
  with a version count; clicking opens the existing versions list.
- Counts use distinct clusters so "Invoices: 12" doesn't inflate to 40 because
  of drafts.
- A view filtered to `clusters: separate` can still show every instance for
  cleanup workflows.

## 8. Slices

| Slice | Contents | Payoff |
|---|---|---|
| 4a | `views` table/CRUD, query builder for concepts (with descendants), doc types, sources, text; list/count; sidebar + builder + save | First real multi-lens browse |
| 4b | facets (topics/entities/date/month), grouping, pinned/colour/icon, empty states | Cheap exploration; drift-aware browsing |
| 4c | semantic views: "similar to this document" (Phase 2b embeddings) and version-cluster views | Organisation beyond keywords |

Each slice ships alone. 4a must not change the tree or assignment semantics.

**Implemented (4a).** `saved_views` stores a `ViewQuery` JSON. Evaluation
supports concept subtrees (`include_descendants`, `any`/`all`), doc types,
topics, sources, text (facts + opt-in FTS), cluster collapse/separate, and
sort. Counts distinct clusters; collapsed pages return one representative per
cluster with a `version_count`. `GET/POST/PATCH/DELETE /views`,
`GET /views/{id}/files`, and `POST /views/preview` are live. The Library
sidebar lists views, the builder creates them with a live preview count, and
the file table shows “N versions”. Editing an existing view is API-ready but
the UI is create/delete for now (4b). Facets/grouping are 4b.

## 9. Acceptance criteria

- Saving a filtered view and reopening it returns the same live set; adding a
  matching document appears without editing the view.
- Concept filters include descendants by default; an exact-concepts toggle
  narrows to the selected nodes.
- Version clusters count once; the list offers the versions.
- Deleting a view leaves files/assignments untouched.
- Facets sum to the view's total for their dimension.
- A view with no matches shows a clear empty state and keeps its filters.
- Tests: query filter combinations, descendant expansion, cluster counting,
  facet aggregates, view CRUD, preview vs saved parity.

## 10. Decisions settled

1. Views are **live queries**, not snapshots.
2. Concept filters include descendants by default.
3. Version clusters count once; the list shows "N versions".
4. Views are global; a view may pin a source.
5. v1 uses **derived facets only**; no user-defined facet schema.

## 11. Non-goals

- A general query language or SQL exposure.
- Per-user/multi-user views (single-user local app).
- Copying or moving files; views never own data.
- RAG/answer synthesis over a view (Tier 3).
- User-defined computed fields beyond the Phase 2 facts.
