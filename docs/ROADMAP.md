# Roadmap notes

## Phase tracker

The long-term direction is "an automated, non-destructive curation layer
over an existing pile of documents": disk stays the source of truth,
identity and derived facts are keyed by content, taxonomy becomes a
governed vocabulary plus cheap re-projectable views, and the result can be
projected back out (to the OS, or as an Obsidian vault) instead of living
only inside Cabinet. Phases are sequential slices, each shippable alone.

| Phase | Theme | Status | Design |
|---|---|---|---|
| 1a | Content-based identity: document/instance split (one document per hash, many path instances), inode+hash move detection, filesystem capability ladder | **Implemented** (backend) | [Phase 1+2 design §3](design/phase-1-2-identity-watching-facts.md) |
| 1b | Folder watching (debounced reconcile, launch/periodic safety nets, removable-drive "unavailable" state + volume rebinding, cloud-placeholder deferral) | **Implemented** (backend + UI; placeholder detection on macOS/Windows) | [Phase 1+2 design §4](design/phase-1-2-identity-watching-facts.md) |
| 2a | Facts cache (Tier 1): per-document summary / entities / topics / provenance locators, fuzzy fingerprint | **Implemented** (summary/type/entities/topics/provenance; fuzzy fingerprint deferred to 2b) | [Phase 1+2 design §5](design/phase-1-2-identity-watching-facts.md) |
| 2b | Document-level embeddings (organisation, not RAG; SQLite, no vector DB), server-side search, opt-in Tier 2 full-text (FTS) index, near-duplicate/version clusters | **Implemented** (embeddings, server-side search, opt-in FTS, SimHash/stem/folder version clusters; cluster category inheritance still a follow-up) | [Phase 1+2 design §5](design/phase-1-2-identity-watching-facts.md) |
| 3 | Concept governance (below): canonical concepts + aliases/synonyms, provisional concepts, consolidation pass, learning from manual corrections | **Implemented** (3a–3d: concepts/aliases/resolution, candidate-ID assignment, review inbox, consolidation/merge, two-pass proposal + drift insights) | [Phase 3 design](design/phase-3-taxonomy-governance.md) |
| 4 | Views, not one tree: multiple saved projections/facets over facts+concepts (topic, document type, project, people, date) — the "dynamic / personal taxonomy" phase, cheap because views are DB lookups over Phase 2 facts | **In progress** (4a saved live views + builder/onboarding; 4b facets/grouping/colours; 4c semantic views pending) | [Phase 4 design](design/phase-4-views.md) |
| 5 | Projecting the view outward (below): portable curation sidecar first, then symlink-tree export, Finder tags, Obsidian-vault export (markdown stub + frontmatter per document) | Discussed (notes below) | — |

Decisions settled 2026-11-22 (Phase 1+2 design doc, §7): D1 watch default
(off for existing sources, on at creation only after 1a soaks); D2
duplicates resolved by the document/instance split — one document per hash,
categories and facts shared across all its path instances, per-instance
manual additions for location-specific filing; D3 tiered storage (Tier 1
facts on for new sources, Tier 2 full-text opt-in, Tier 3 RAG chunking out
of scope; facts carry page/sheet provenance and originals open in their own
tool); D7 filesystem capability ladder (corroborated inode on APFS/NTFS/ext4,
hash-on-change on FAT/exFAT) plus volume-identity rebinding and an honest
"unavailable" state for disconnected drives; D4 cloud-placeholder detection
via `SF_DATALESS`/Windows recall attributes, with implicit reads deferred;
D5 separate embedding config defaulting to local Ollama `nomic-embed-text`
(Anthropic rejected for embeddings). Still open: D6 tombstone retention/GC
period, and Phase 3's D8–D12 in its design doc.

### Independent backlog (no phase dependency)

- OCR fallback when a PDF extracts near-zero text; PPTX extractor.
- API keys into the OS keychain (Electron `safeStorage`) instead of
  plaintext SQLite.
- Frontend tests (none today; backend has pytest coverage).

## Phase 3 — Taxonomy governance — "bounded structure, unbounded vocabulary" (designed)

Design: [phase-3-taxonomy-governance.md](design/phase-3-taxonomy-governance.md)
(draft for review; open decisions D8–D12: category-vs-concept table, alias
uniqueness scope, promotion threshold, concept-embedding thresholds,
consolidation cadence). Slices 3a–3d are defined there.

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

Additional Phase 3 mechanisms discussed Nov 2026 (full rationale in the
[Phase 1+2 design](design/phase-1-2-identity-watching-facts.md), Appendix A
and §8 non-goals):

- Concepts as first-class rows: stable ID, canonical name, definition, and
  an **alias/synonym list** (SKOS-style broader/narrower/related). A merge
  is then an alias addition + assignment repoint — pure DB updates, no
  file reprocessing. Antonyms aren't useful here; aliases and
  "not to be confused with" notes in definitions are.
- Resolve LLM-proposed names in order: exact slug → alias → stemmed match
  → embedding similarity against concept names+definitions (auto-alias at
  very high similarity, human queue in the middle band) → new
  **provisional** concept, auto-promoted after N files or approved in a
  low-friction review inbox.
- Constrained assignment: send the LLM the nearest candidate concepts (by
  Phase 2b embeddings) to pick from by ID, instead of the whole tree in
  every prompt.
- Learn from manual corrections: store them as examples and inject the
  nearest few into future assignment prompts. Standardise the *learning
  loop*, not the vocabulary — that is how the taxonomy becomes personal.
- Drift metrics: singleton rate, concepts per 100 files, pending
  near-duplicate pairs.

## Phase 5 — Projecting the virtual view back into the OS

Non-destructive alternatives to moving files, discussed July 2026. Ordered
preference as of Nov 2026: **portable curation sidecar first** (manual
assignments/merges written to disk keyed by content hash, so the DB is a
rebuildable cache and human curation survives losing it), then the two
options below, then an **Obsidian-vault export** (one markdown stub per
document: facts as frontmatter, concepts as tags, link to the original) —
Cabinet as the automated ingestion/gardening layer in front of a vault the
user owns as plain text.

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
