# CLAUDE.md

## Project Overview

Cabinet is a desktop app that uses AI to categorize files into virtual folders. Electron frontend talks to a Python FastAPI backend over HTTP on port 8420.

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

- Text extraction is **ephemeral** -- extracted content is held in a `dict[int, str]` during pipeline processing, passed to the categorizer, then discarded. No text is stored in the database.
- File status flows: `pending` -> `extracted` -> `categorized`
- Files are processed in **batches of 10** (configurable via `categorization_batch_size`)
- Search is **client-side** -- filters on filename and category name using already-loaded data
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
