#!/usr/bin/env python3
"""PyInstaller entry point for Cabinet API."""
import multiprocessing

multiprocessing.freeze_support()

import argparse
import os
import sys
import tempfile

print("Starting Cabinet API...", file=sys.stderr, flush=True)

# Set up temp directory for PyInstaller bundle
if getattr(sys, "frozen", False):
    system_temp = tempfile.gettempdir()
    app_temp = os.path.join(system_temp, "cabinet-temp")
    os.makedirs(app_temp, exist_ok=True)
    os.environ["TMPDIR"] = app_temp
    os.environ["TEMP"] = app_temp
    os.environ["TMP"] = app_temp
    print(f"Temp directory set to: {app_temp}", file=sys.stderr, flush=True)

try:
    import uvicorn

    print("Uvicorn imported", file=sys.stderr, flush=True)
    from cabinet.app import create_app

    print("App factory imported", file=sys.stderr, flush=True)
except Exception as e:
    import traceback

    print(f"Import error: {e}", file=sys.stderr, flush=True)
    traceback.print_exc()
    sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cabinet API Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8420, help="Port to bind to")
    args = parser.parse_args()

    app = create_app()
    print(f"Starting server on {args.host}:{args.port}", file=sys.stderr, flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
