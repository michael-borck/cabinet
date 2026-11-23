#!/usr/bin/env bash
# Notarize and staple every DMG in a directory (macOS only).
#
# The app inside is normally already notarized/stapled by electron-builder.
# Stapling the DMG container lets Gatekeeper validate the download itself and
# avoids a network check when the disk image is opened.
#
# Expects the app-hq API-key variables:
#   APPLE_API_KEY       path to AuthKey_<id>.p8 (electron-builder builtin mode)
#   APPLE_API_KEY_ID    Key ID
#   APPLE_API_ISSUER    Issuer ID
set -euo pipefail

TARGET_DIR="${1:-release}"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "Skipping DMG notarization: not macOS."
  exit 0
fi

shopt -s nullglob
dmgs=("$TARGET_DIR"/*.dmg)
if [ ${#dmgs[@]} -eq 0 ]; then
  echo "No DMGs found in $TARGET_DIR; nothing to notarize."
  exit 0
fi

if [ -z "${APPLE_API_KEY:-}" ] || [ -z "${APPLE_API_KEY_ID:-}" ] || [ -z "${APPLE_API_ISSUER:-}" ]; then
  echo "Skipping DMG notarization: APPLE_API_KEY, APPLE_API_KEY_ID, or APPLE_API_ISSUER is unset."
  exit 0
fi

if [ -z "${APPLE_SIGNING_IDENTITY:-}" ]; then
  echo "Skipping DMG signing/notarization: APPLE_SIGNING_IDENTITY is unset."
  exit 0
fi

for dmg in "${dmgs[@]}"; do
  echo "Signing $dmg"
  codesign --force --timestamp --sign "$APPLE_SIGNING_IDENTITY" "$dmg"
  echo "Notarizing $dmg"
  xcrun notarytool submit "$dmg" \
    --key "$APPLE_API_KEY" \
    --key-id "$APPLE_API_KEY_ID" \
    --issuer "$APPLE_API_ISSUER" \
    --wait
  xcrun stapler staple "$dmg"
  xcrun stapler validate "$dmg"
done
