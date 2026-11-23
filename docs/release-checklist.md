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

## 3b. Local signed + notarized build (macOS)

The fleet uses one Developer ID and one App Store Connect API key
(`app-hq/runbooks/signing.md`). The certificate is in the login keychain;
`~/.certs/apple.env` holds the identity, team, key path, Key ID, and issuer.

```bash
set -a; source ~/.certs/apple.env; set +a
export APPLE_API_KEY_ID="$APPLE_API_KEY"   # Key ID
export APPLE_API_KEY="$APPLE_API_KEY_P8"   # .p8 path for built-in notarization

# Host arch (arm64 on this Mac); CI runs the same on an Intel leg.
npx electron-builder --mac --publish never \
  -c.directories.output=/tmp/cabinet-signed
bash scripts/notarize-dmg.sh /tmp/cabinet-signed

APP=/tmp/cabinet-signed/mac-arm64/Cabinet.app
codesign --verify --deep --strict --verbose=2 "$APP"
spctl --assess --type execute --verbose=4 "$APP"   # Notarized Developer ID
xcrun stapler validate "$APP"
xcrun stapler validate /tmp/cabinet-signed/Cabinet-*-arm64.dmg
spctl -a -vvv -t open --context context:primary-signature \
  /tmp/cabinet-signed/Cabinet-*-arm64.dmg
```

- CI uses `app-hq/.github/workflows/electron-release.yml` with
  `signing-mode: custom` and `notarize-mode: builtin`; the shared
  `electron-keychain` and `apple-signing` actions import the certificate and
  write the API key. `package-command` runs `electron-builder --mac`.
- The `.app` is notarized and stapled. `scripts/notarize-dmg.sh` (wired as
  the workflow's `post-package-command`) also signs, notarizes, and staples
  each DMG, so the downloaded disk image itself passes `spctl` and validates
  offline.
  Local check:

  ```bash
  bash scripts/notarize-dmg.sh /tmp/cabinet-signed
  xcrun stapler validate /tmp/cabinet-signed/Cabinet-*-arm64.dmg
  spctl -a -vvv -t open --context context:primary-signature \
    /tmp/cabinet-signed/Cabinet-*-arm64.dmg
  ```
- ExFAT project volumes create AppleDouble `._*` files. A
  `._cabinet-api` inside the bundle broke notarization; `electron-builder.yml`
  now excludes `._*`/`.DS_Store` from `files` and `extraResources`. Keep the
  exclusion when editing packaging config.

## 4. Manual smoke test

1. **First run:** launch the app. It shows the welcome state; add a folder;
   configure an LLM; optionally configure the embedding model (default
   Ollama `nomic-embed-text`); run a sync from Settings → Sources.
2. **Catalogue:** files appear; facts show in the details panel; categories
   and provisional concepts appear; assign a category manually.
3. **Views:** create a view (concepts/doc types/text), preview, save; verify
   filter chips, facets, group-by counts, and “N versions”.
   Configure an LLM with an API key, quit, and confirm the raw
   `llm_configs.api_key` starts with `enc:v1:`; relaunch and check that the
   provider still works.
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

- **Signing/notarisation:** macOS release builds are signed/notarized through
  app-hq. Windows/Linux artifacts remain unsigned unless Azure Trusted
  Signing is added, so `electron-updater` cannot verify them there.
- **API keys at rest:** packaged builds encrypt stored keys with a random
  key protected by Electron `safeStorage` (OS keychain). The key lives at
  `<userData>/backend-key.bin`; losing it means re-entering API keys. Dev and
  standalone runs without `CABINET_ENCRYPTION_KEY` store keys unencrypted (the
  LLM form says so). Existing plaintext rows are migrated on startup.
- **Windows symlink fallback:** symlink-tree export reports unprivileged
  Windows/FAT/exFAT clearly but does not create `.lnk` shortcuts yet.
- **Multi-arch macOS:** each macOS runner builds its host architecture and a
  matching single-arch sidecar. The CI matrix has `macos-15` (arm64) and
  `macos-15-intel` (x64) legs; `scripts/package.sh` maps the `mac-intel`
  matrix label to `electron-builder --mac`, which builds the runner's host
  arch because the config has no explicit arch list. Never bundle one arch's
  sidecar into the other app.
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
- At-rest key: `<userData>/backend-key.bin` (safeStorage-protected).
- Web assets: `Cabinet.app/Contents/Resources/app.asar`.
- Python backend: `Cabinet.app/Contents/Resources/backend/cabinet-api`.
- Backend temp (PyInstaller): `<system temp>/cabinet-temp`.
- Backend logs: captured on the Electron main process stdout/stderr.
