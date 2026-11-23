# CLAUDE.md

## Project Overview

Cabinet is a desktop app that uses AI to categorize files into virtual folders. Electron main picks a free local port per launch (8420 when run standalone) and the renderer talks to the Python FastAPI backend over HTTP on it.

## Architecture

```
├── backend/src/cabinet/    Python package (FastAPI + SQLAlchemy)
│   ├── db/                 Models, engine, repositories
│   ├── extractors/         PDF, DOCX, XLSX, plain text
│   ├── llm/                Anthropic, OpenAI, Gemini, Ollama providers
│   ├── routers/            API endpoints (sources, files, categories, jobs, settings)
│   ├── schemas/            Pydantic request/response models
│   ├── services/           Pipeline, categorization, scanner
│   └── utils/              Hashing, text helpers
├── src/                    Electron + React frontend
│   ├── main/               Electron main process (spawns backend)
│   ├── preload/            Electron preload scripts
│   └── renderer/           React app (components, hooks, stores, services)
└── backend/tests/          pytest test suite
```

## Key Design Decisions

- Text extraction is **ephemeral** at Tier 0 -- extracted content is held in a `dict[int, str]` during pipeline processing, passed to the facts pass/categorizer, then discarded. Raw text is never stored. With Phase 2a Tier 1 (`sources.store_facts`, on for new sources), AI-derived facts (summary, doc type, entities, topics, provenance) are kept per document hash in `content_facts`; turning the flag off purges facts no enabled source references. When a separate embedding config is active (`LLMConfig.kind="embedding"`, default local Ollama `nomic-embed-text`), one float32 vector per document is stored on the same row; embedding failures are non-fatal. Tier 2 full-text indexing is opt-in per source (`sources.store_text_index`, off by default) in the `content_text_fts` FTS5 table, purged when disabled. Near-duplicate/version grouping uses a SimHash `content_facts.fingerprint` + normalized filename stem + same-folder corroboration to set `documents.cluster_id` (grouped, never merged).
- File status flows: `pending` -> `extracted` -> `categorized`
- Files are processed in **batches of 10** (configurable via `categorization_batch_size`)
- Search is **server-side** -- `GET /files?q=` matches filename/path and cached facts, plus opt-in Tier-2 body text; the renderer search box queries it instead of loading every file
- Categories are governed **concepts** (Phase 3): resolution order is exact slug -> globally unique alias -> stemmed/plural -> embedding similarity -> new provisional concept. AI assignments pick candidate concepts by ID (Phase 2b document embeddings), manual corrections write `category_examples`, and provisional concepts auto-promote at `concept_promotion_threshold` (default 3 documents) or via the Review inbox (`GET /categories/review`, approve/reject/merge). `POST /categories/consolidate` proposes deterministic + LLM merges/renames (never auto-applied); `POST /categories/{id}/merge` repoints assignments/aliases/examples as pure DB updates and marks the loser `merged` (hidden from browse, kept for history). Renames recompute the slug and invalidate the concept embedding. Two-pass taxonomy proposal (`POST /categories/propose` then `/propose/apply`) and drift insights (`GET /insights/taxonomy`, Insights tab) complete Phase 3; consolidation remains on-demand (the automatic every-N-documents cadence is not wired).
- Views (Phase 4a) are saved **live queries** in `saved_views` (a `ViewQuery` JSON; no file copies): concept subtrees, doc types, topics, sources, text, cluster collapse, sort. `/views` CRUD + `/views/preview`, `GET /views/{id}/files` returns cluster-aware counts and `version_count`. The Library sidebar lists views and a builder creates/edits them (pin, colour, group-by, versions filter, live preview); the file table shows filter chips, a group-by selector with counts, a facet summary (types/topics/sources/months), and “N versions”, with first-run/empty states. `GET /files/{id}/similar` ranks documents by embedding cosine and the details panel links to matches; `ViewQuery.versions` filters clustered vs singleton documents. Icon selection remains unwired.
- The durable curation artifact (Phase 5a) is a portable JSON sidecar (`CABINET_CURATION_PATH`, default `curation.json` beside the DB): non-merged concepts with definitions/aliases, merges, manual document assignments keyed by content hash, and saved views. `GET /curation/export` / `POST /curation/import` apply it additively; the app also writes it atomically on shutdown. Raw text, facts, embeddings, and keys are not included.
- Folder exports (Phase 5b): `POST /exports/symlink-tree/plan|apply` projects the concept tree (or one saved view) into symlinks under a chosen target; a `.cabinet-export.json` manifest tracks generated links so refresh only removes/updates its own entries and never touches other files. A symlink probe reports unsupported filesystems (FAT/exFAT, unprivileged Windows) clearly; the Windows `.lnk` fallback is not wired. Settings → Export provides target/scope, preview, and apply. Finder tags (Phase 5c): `POST /exports/finder-tags/plan|apply` additively writes concept names to `com.apple.metadata:_kMDItemUserTags` on macOS (libc xattr via ctypes; existing tags/colours preserved); non-macOS reports unsupported. Obsidian vault (Phase 5d): `POST /exports/obsidian/plan|apply` writes create-only markdown catalogue cards under `<target>/Cabinet/` with facts as frontmatter, concepts as slugged tags, and a `file://` link to the original; existing notes are never overwritten.
- The database is SQLite with async access via aiosqlite
- The scanner always skips hidden entries and a built-in set of development/junk names (`DEFAULT_EXCLUDED_DIRS` / `DEFAULT_EXCLUDED_FILES` in `services/scanner.py`); each source can add glob `exclude_patterns` (matched against bare names and source-relative paths), editable via `PATCH /sources/{id}` and the Sources tab

## Commands

### Backend (run from `backend/`)

```bash
uv run ruff check .              # Lint
uv run pytest -v                 # Tests (in-memory SQLite, no external deps)
uv run uvicorn cabinet.app:create_app --factory --port 8420  # Run standalone
```

### Frontend (run from project root)

```bash
npm install                      # Install deps
npx tsc --noEmit                 # Type check
npx electron-vite build          # Production build
npm run dev                      # Dev mode (starts Electron + backend)
```

## Environment Variables

All prefixed with `CABINET_`:

- `CABINET_DB_PATH` -- SQLite database file path (default: `backend/data/cabinet.db`)
- `CABINET_EXTRACTION_MAX_CHARS` -- Max chars to extract per file (default: 50000)
- `CABINET_CATEGORIZATION_BATCH_SIZE` -- Files per LLM call (default: 10)
- `CABINET_API_TOKEN` -- When set, every API request (except `/health`) must send it in the `X-API-Token` header. Electron generates a fresh token per launch and injects it into both processes; unset (e.g. standalone uvicorn, tests) the API is open.

## Conventions

- Python: ruff for linting, line length 100, target Python 3.12
- Routers suppress B008 (function call in default arg) for FastAPI `Depends()`
- Tests use in-memory SQLite with dependency injection -- no test database on disk
- Frontend state: Zustand for UI state, TanStack Query for server state
- Imports use `@/` alias pointing to `src/renderer/`
