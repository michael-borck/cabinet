#!/usr/bin/env bash
# Build the cabinet-api PyInstaller sidecar and stage it for Electron.
set -euo pipefail

ROOT="$(pwd)"
if [ "${RUNNER_OS:-}" = "Windows" ]; then
  EXT=".exe"
  SEP=";"
else
  EXT=""
  SEP=":"
fi

python -m pip install --disable-pip-version-check uv
uv python install 3.12
rm -rf .venv-sidecar
uv venv --python 3.12 --seed .venv-sidecar
if [ -f .venv-sidecar/bin/activate ]; then
  source .venv-sidecar/bin/activate
else
  source .venv-sidecar/Scripts/activate
fi

cd backend
python -m pip install --upgrade pip
uv export --frozen --no-dev --no-emit-project --format requirements-txt -o requirements-ci.txt
uv pip install -r requirements-ci.txt
uv pip install --no-deps .
pip install pyinstaller
python -c "import greenlet; print(f'greenlet {greenlet.__version__} at {greenlet.__file__}')"

pyinstaller --onefile \
  --console \
  --name "cabinet-api" \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.lifespan \
  --hidden-import uvicorn.lifespan.on \
  --hidden-import uvicorn.lifespan.off \
  --hidden-import uvicorn.protocols \
  --hidden-import uvicorn.protocols.http \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.http.h11_impl \
  --hidden-import uvicorn.protocols.http.httptools_impl \
  --hidden-import uvicorn.protocols.websockets \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.protocols.websockets.websockets_impl \
  --hidden-import uvicorn.protocols.websockets.wsproto_impl \
  --hidden-import uvicorn.loops \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.loops.asyncio \
  --hidden-import uvicorn.loops.uvloop \
  --hidden-import fastapi \
  --hidden-import pydantic \
  --hidden-import pydantic_settings \
  --hidden-import sqlalchemy \
  --hidden-import sqlalchemy.dialects.sqlite \
  --hidden-import aiosqlite \
  --hidden-import greenlet \
  --collect-all greenlet \
  --collect-binaries greenlet \
  --hidden-import sse_starlette \
  --hidden-import pymupdf \
  --hidden-import pymupdf4llm \
  --hidden-import fitz \
  --hidden-import docx \
  --hidden-import openpyxl \
  --hidden-import anthropic \
  --hidden-import openai \
  --hidden-import google.genai \
  --hidden-import python_multipart \
  --hidden-import multipart \
  --hidden-import starlette.formparsers \
  --hidden-import watchdog \
  --hidden-import watchdog.events \
  --hidden-import watchdog.observers \
  --hidden-import watchdog.observers.fsevents \
  --hidden-import watchdog.observers.inotify \
  --hidden-import watchdog.observers.read_directory_changes \
  --hidden-import watchdog.observers.polling \
  --exclude-module torch \
  --exclude-module tensorflow \
  --exclude-module keras \
  --exclude-module matplotlib \
  --exclude-module PIL \
  --exclude-module cv2 \
  --exclude-module numpy \
  --exclude-module scipy \
  --exclude-module pandas \
  run_server.py

./dist/cabinet-api$EXT --port 19999 > server.log 2>&1 &
PID=$!
ready=0
for _ in $(seq 1 120); do
  if curl -s http://127.0.0.1:19999/api/v1/health >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "Server process died!"
    cat server.log || true
    exit 1
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  echo "Health check timed out!"
  cat server.log || true
  kill "$PID" 2>/dev/null || true
  exit 1
fi
curl -f http://127.0.0.1:19999/api/v1/health
kill "$PID" 2>/dev/null || true
wait "$PID" 2>/dev/null || true

mkdir -p "$ROOT/backend-dist"
cp "dist/cabinet-api$EXT" "$ROOT/backend-dist/cabinet-api$EXT"
ls -la "$ROOT/backend-dist/"
