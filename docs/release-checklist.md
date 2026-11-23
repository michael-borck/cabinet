# Release / QA checklist

Run this before tagging a build. It covers the automated checks, an unsigned
packaging smoke test, and the manual paths that are hard to unit-test.

## 1. Automated checks

```bash
# Backend
cd backend
uv run ruff check .
uv run pytest -q

# Frontend / packaging
cd ..
npm run typecheck
npm test
npm run build        # electron-vite production build
```

Expected: ruff clean, all backend tests green, Vitest green, typecheck clean,
`out/main`, `out/preload`, `out/renderer` produced.

## 2. Build the Python sidecar

Build on an APFS volume. The project may live on exFAT; installing the
sidecar venv there fails (AppleDouble `._` files) and PyInstaller writes
can corrupt.

```bash
bash scripts/build-sidecar.sh
# verify the staged binary
ls -l backend-dist/cabinet-api
```

The script installs a Python 3.12 virtualenv, builds a one-file PyInstaller
binary, health-checks it, and copies it to `backend-dist/`. The sidecar is
**single-arch** (the host arch), so build one app per arch.

If the project is on an external exFAT drive, copy `backend/` plus
`scripts/build-sidecar.sh` to `/tmp` (APFS), run the script there, then copy
`backend-dist/cabinet-api` back.

## 3. Package the app (unsigned QA build)

The output must be on APFS: electron-builder writes `app.asar` into the output
directory and exFAT produced a corrupted asar header in QA.

```bash
CSC_IDENTITY_AUTO_DISCOVERY=false \
  npx electron-builder --mac --arm64 --dir \
  -c.directories.output=/tmp/cabinet-release
```

Then verify:

```bash
APP=/tmp/cabinet-release/mac-arm64/Cabinet.app
ls -l "$APP/Contents/Resources/backend/cabinet-api"
ls -l "$APP/Contents/Resources/app.asar"
/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$APP/Contents/Info.plist"
```

Run the bundled sidecar exactly as the app will (userData-style paths):

```bash
mkdir -p /tmp/cabinet-qa
CABINET_DB_PATH=/tmp/cabinet-qa/cabinet.db \
CABINET_CURATION_PATH=/tmp/cabinet-qa/curation.json \
CABINET_WATCH_ENABLED=false \
"$APP/Contents/Resources/backend/cabinet-api" --port 19998 --host 127.0.0.1
curl -s http://127.0.0.1:19998/api/v1/health
```

Stop it (SIGTERM) and confirm `curation.json` is written next to the DB.

## 4. Manual smoke test

1. **First run:** launch the app. It shows the welcome state; add a folder;
   configure an LLM; optionally configure the embedding model (default
   Ollama `nomic-embed-text`); run a sync from Settings → Sources.
2. **Catalogue:** files appear; facts show in the details panel; categories
   and provisional concepts appear; assign a category manually.
3. **Views:** create a view (concepts/doc types/text), preview, save; verify
   filter chips, facets, group-by counts, and “N versions”.
4. **Curation:** review inbox approve/reject/merge; run consolidation;
   propose a taxonomy and apply it; check insights.
5. **Exports:** symlink-tree export to an APFS folder (preview → apply →
   re-apply keeps); Finder tags on a scratch file (`xattr -p
   com.apple.metadata:_kMDItemUserTags <file>`); Obsidian export to a scratch
   vault, inspect a note, re-apply reports conflicts.
6. **Restart:** quit the app; confirm `curation.json` beside the DB. Relaunch;
   the DB persists under the app's userData directory and the UI reconnects
   on a new free port.
7. **Failure paths:** quit the backend while the app runs (banner appears);
   start the app with the port in use (it picks another); export to an
   unsupported filesystem (clear message).

## 5. Known ship risks / follow-ups

- **Signing/notarisation:** QA builds are unsigned; Windows/Linux artifacts
  are unsigned, so `electron-updater` cannot verify them. Configure a signing
  identity and notarisation before release.
- **API keys:** stored in the local SQLite database in plaintext (the LLM form
  says so). OS-keychain storage via Electron `safeStorage` is still pending.
- **App icon:** `resources/icon.{icns,ico,png}` is not present yet, so the
  build uses Electron's default icon. Add a real icon set.
- **Windows symlink fallback:** symlink-tree export reports unprivileged
  Windows/FAT/exFAT clearly but does not create `.lnk` shortcuts yet.
- **Multi-arch macOS:** the sidecar is single-arch; build the sidecar and the
  app per arch. Do not bundle one sidecar into a universal build.
- **Linux/BSD cloud placeholders:** no portable dataless marker; files on
  cloud FUSE mounts behave as local files.
- **Obsidian export:** create-only (no updates) and no vault index note;
  symlink view exports are flat (no group-by subfolders).
- **Curation sidecar:** manual document-level assignments only; per-instance
  additions are not included yet.
- **Background work:** automatic taxonomy consolidation cadence is not wired
  (on-demand only); OCR fallback and PPTX extraction are not implemented.
- **Tests:** frontend coverage is a small Vitest smoke suite; the bulk of the
  logic is covered by backend tests.
- **exFAT source volume:** `._*` AppleDouble files appear next to edited files
  and can confuse glob-based tools; they are git-ignored now. Build venvs and
  app output on APFS.

## 6. Data locations

- Production DB: `<userData>/cabinet.db` (set by Electron main).
- Curation sidecar: `<userData>/curation.json`.
- Web assets: `Cabinet.app/Contents/Resources/app.asar`.
- Python backend: `Cabinet.app/Contents/Resources/backend/cabinet-api`.
- Backend temp (PyInstaller): `<system temp>/cabinet-temp`.
- Backend logs: captured on the Electron main process stdout/stderr.
