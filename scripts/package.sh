#!/usr/bin/env bash
# Cross-platform packaging entry used by the app-hq shared Electron workflow.
# PLATFORM is set by the workflow matrix: mac | mac-intel | linux | win.
# electron-builder defaults to the runner's host architecture, which matches
# the sidecar built on the same runner.
set -euo pipefail

case "${PLATFORM:?PLATFORM is required}" in
  mac|mac-intel)
    npx electron-builder --mac --publish never
    ;;
  win)
    npx electron-builder --win --publish never
    ;;
  linux)
    npx electron-builder --linux --publish never
    ;;
  *)
    echo "Unknown PLATFORM: ${PLATFORM}" >&2
    exit 1
    ;;
esac
