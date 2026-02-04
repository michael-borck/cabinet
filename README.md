# Cabinet

A desktop app that uses AI to automatically categorize your files into smart virtual folders. Point it at a directory, and Cabinet extracts text from your documents, sends them to an LLM, and organizes everything into a clean category tree -- without moving a single file.

## Features

- **AI-powered categorization** -- files are automatically tagged with hierarchical categories (e.g. "Finance > Invoices")
- **Virtual folders** -- files can belong to multiple categories; nothing is moved on disk
- **Multiple LLM providers** -- Anthropic, OpenAI, Google Gemini, or local models via Ollama
- **Document extraction** -- PDF, DOCX, XLSX, and plain text
- **Client-side search** -- filter files by name or category instantly
- **Batch processing** -- files are processed in configurable batches for efficiency

## Tech Stack

| Layer | Stack |
|-------|-------|
| Frontend | Electron 33, React 19, Tailwind CSS 4, Zustand, TanStack Query |
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2.0, aiosqlite |
| LLM | Anthropic, OpenAI, Gemini, Ollama |
| Extractors | PyMuPDF, python-docx, openpyxl |

## Getting Started

### Prerequisites

- Node.js 18+
- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python package manager)

### Setup

```bash
# Install frontend dependencies
npm install

# Install backend dependencies
cd backend
uv sync
cd ..
```

### Development

```bash
# Start the Electron app (launches both frontend and backend)
npm run dev
```

### Running the backend standalone

```bash
cd backend
uv run uvicorn cabinet.app:create_app --factory --port 8420
```

### Tests

```bash
cd backend
uv run pytest -v
```

### Lint

```bash
cd backend
uv run ruff check .
```

## How It Works

1. **Add a source directory** -- Cabinet scans it and indexes file metadata
2. **Configure an LLM** -- pick a provider and model in Settings
3. **Run a sync** -- Cabinet extracts text, sends batches to the LLM, and stores category assignments
4. **Browse and search** -- explore your files through the category tree or search by filename/category

Text content is only held in memory during processing and is never stored in the database.

## License

[MIT](LICENSE)
