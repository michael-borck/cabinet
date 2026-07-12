# Roadmap notes

## Taxonomy governance — "bounded structure, unbounded vocabulary" (revisit)

The category system is a tree, so it cannot degrade into a graph hairball — but the
*vocabulary* is unbounded and will drift:

- **Near-duplicate synonyms** — the LLM sees the existing tree in its prompt but nothing
  forces reuse ("Finance" vs "Finances", "Invoices" vs "Billing").
- **Path dependence** — files are categorized in batches of 10, so the first batches
  define the taxonomy and later files bend to fit it.
- **Singleton sprawl** — categories with one file each.

Mitigations to evaluate, roughly in order of effort:

1. Prompt constraints: max tree depth, max children per node, "strongly prefer existing
   categories" instruction.
2. Consolidation pass: periodically ask the LLM to review the tree and propose merges,
   presented to the user for approval before applying.
3. Two-pass categorization: first propose a taxonomy from a sample of the whole corpus,
   then assign files to it — removes path dependence entirely.

Goal: category count should grow with content *diversity*, not file *count*.

## Projecting the virtual view back into the OS

Non-destructive alternatives to moving files, discussed July 2026:

- **Materialized symlink tree** — "Export as folders" builds a directory tree of
  symlinks mirroring the categories. On Windows, try `fs.symlink` (works with Developer
  Mode/admin) and fall back to `shell.writeShortcutLink` `.lnk` files. Validate the
  target filesystem: exFAT/FAT32 support neither symlinks nor hardlinks.
- **macOS Finder tags** — write categories as native tags (xattr) so Finder and
  Spotlight (`tag:Invoices`) can filter without Cabinet running.

## Accepted risks (documented, not fixed)

- **macOS entitlements** (`build/entitlements.mac.plist`): `allow-unsigned-executable-memory`
  and `disable-library-validation` weaken the hardened runtime but are required for the
  PyInstaller-bundled backend to launch. Revisit if the backend ships differently.
- **Windows/Linux update signing**: artifacts are unsigned pending a code-signing
  certificate; electron-updater therefore cannot verify signatures on those platforms.
