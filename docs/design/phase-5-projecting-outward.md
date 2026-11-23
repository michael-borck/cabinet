# Design: Phase 5 — Projecting the view back out

- **Status:** 5a–5d implemented (curation sidecar; symlink-tree export;
  Finder tags; Obsidian vault export). Follow-ups noted in each slice.
- **Date:** 2026-11-23
- **Scope:** Non-destructive ways to take Cabinet's curation back into the
  user's own tools: a portable curation sidecar, a materialized symlink tree,
  macOS Finder tags, and an Obsidian vault export.
- **Related:** [Phase 1+2](phase-1-2-identity-watching-facts.md),
  [Phase 3](phase-3-taxonomy-governance.md), [Phase 4](phase-4-views.md),
  [ROADMAP](../ROADMAP.md).

## 1. The problem

Everything Cabinet knows lives in a local SQLite database. The files are
untouched, but the *curation* — manual assignments, concepts, aliases,
merges, saved views — would be lost with the database. And the user's other
tools (Finder, Obsidian, an editor) cannot see the virtual structure.

Phase 5 makes the curation portable and, later, projects it into the OS.

## 2. Principles

1. **Non-destructive.** Never move, rename, or rewrite source files. The only
   optional writes are sidecars and export artifacts in explicit locations.
2. **The database is a cache.** Human curation should survive losing it; the
   sidecar is the durable, portable artifact.
3. **Explicit targets.** The user chooses where an export goes. Nothing is
   written into a source folder by default.
4. **Idempotent and incremental.** Re-running an export/import must not
   duplicate or destroy curation.
5. **Dry-run first.** Destructive-looking exports (symlinks, tags) show what
   would change before applying.

## 3. 5a — Portable curation sidecar

A single JSON document keyed by content hash, not by path:

```json
{
  "version": 1,
  "generated_at": "2026-11-23T12:00:00Z",
  "concepts": [
    {"path": "Finance", "name": "Finance", "definition": "Money",
     "color": "#3b82f6", "status": "approved", "aliases": ["Billing"]}
  ],
  "merges": [
    {"source_path": "Bills", "target_path": "Finance > Invoices"}
  ],
  "assignments": [
    {"content_hash": "sha256:…", "concept_path": "Finance > Invoices"}
  ],
  "views": [
    {"name": "Q1 invoices", "query": {"doc_types": ["invoice"]},
     "pinned": false, "color": "#f59e0b"}
  ]
}
```

Included:
- every non-merged concept (approved or provisional) with definition, colour,
  status, and aliases;
- merged concepts as source-path → target-path pairs;
- **manual document-level** assignments (`is_manual`), keyed by content hash;
- saved views (name, query, pin, colour).

Not included: raw text, facts, embeddings, API keys, per-instance-only manual
additions (path-dependent; follow-up), job history.

### 3.1 Location and writes

- `CABINET_CURATION_PATH` when set; otherwise `curation.json` next to the
  database file.
- The sidecar is written **on app shutdown** and by an explicit **Export**
  action. Writes are atomic (temp file + rename) and best-effort: a failure is
  logged, never fatal.
- Import is explicit (Settings → Export/Import); there is no silent
  auto-restore. After a DB loss, the user re-scans and imports the sidecar.

### 3.2 Import rules

- Concepts: resolve/create by canonical path; fill an empty definition or
  colour; add aliases if free; keep existing values on conflict.
- Merges: applied only when both sides resolve and the source is not already
  merged; otherwise reported.
- Assignments: resolve the content hash to an existing document (skip and
  report unknown hashes — the user may not have re-scanned yet); resolve the
  concept path and ensure a manual document assignment.
- Views: create when the name is new; existing views win.
- Idempotent: a second import changes nothing.

**Implemented (5a).** `GET /curation/export` returns the sidecar document;
`POST /curation/import` applies it additively and reports counts plus capped
warnings. The sidecar is also written atomically on app shutdown to
`CABINET_CURATION_PATH` (default `curation.json` beside the DB). Settings →
Sources has Export/Import JSON actions. Only manual document-level assignments
are included; per-instance additions remain a follow-up. A merge whose source
name is already an alias of the target restores as that alias (the effective
taxonomy is identical).

## 4. 5b — Materialized symlink tree

“Export as folders” builds a directory tree of symlinks mirroring the current
view or concept tree. It is a projection, not a move:

- Choose a target directory, scope (view or whole tree), and refresh mode.
- Symlinks point at the originals; refreshing replaces the generated links
  but never deletes non-generated files (a manifest marks ours).
- Windows: try `fs.symlink` (Developer Mode/admin), fall back to `.lnk` via
  `shell.writeShortcutLink`.
- Reject FAT/exFAT targets (no symlinks/links) with a clear message.
- Dry-run summary: links to create/update/remove.

**Implemented (5b).** `POST /exports/symlink-tree/plan` returns a dry-run
(create/update/remove/keep/conflict counts plus sampled actions) and `POST
/exports/symlink-tree/apply` materializes it. Library scope mirrors the
concept tree (`finance/invoices/…`) with unassigned documents under
`_uncategorised/`; view scope writes `<view-slug>/…`. A `.cabinet-export.json`
manifest tracks generated links, so refresh only touches its own entries;
regular files at a desired path are reported as conflicts, never overwritten.
A symlink probe turns FAT/exFAT (and unprivileged Windows) into a clear
“symlinks unavailable” message. The Windows `.lnk` fallback is not wired yet.
Settings → Export provides target/scope selection, preview, and apply.

## 5. 5c — Finder tags (macOS)

Write concepts (or the current view's concepts) as native Finder tags via
xattrs (`com.apple.metadata:_kMDItemUserTags`). One-way export first; import
later. Limits: a handful of visible tags per file; document the macOS-only
scope and keep it opt-in.

**Implemented (5c).** `POST /exports/finder-tags/plan|apply` adds each live
file's document-level and per-instance concept names as Finder tags. macOS
exposes no `os.setxattr`, so the service calls libc's six-argument
`setxattr`/`getxattr` via ctypes. Writes are **additive**: existing tags
(and their colour suffix) are preserved; stale Cabinet tags are not removed
in this slice. Missing files, cloud placeholders, and non-macOS platforms are
skipped or reported. Settings → Export has Preview/Apply tags.

## 6. 5d — Obsidian vault export

One markdown stub per document in a chosen vault:

- frontmatter from facts (summary, doc type, topics, source, hash);
- concepts as tags;
- a link to the original file;
- optional saved view as an index note.

No raw text body by default (Tier 2 is separate); the stub is a catalogue
card that makes Cabinet the ingestion/gardening layer in front of a vault the
user owns as plain text.

**Implemented (5d).** `POST /exports/obsidian/plan|apply` writes create-only
notes under `<target>/Cabinet/`. Each note has frontmatter for hash, filename,
path, source, doc type, language, topics, summary, full concept paths, and
slugged Obsidian tags (`finance/invoices`), plus a `file://` link to the
original. Library scope covers every document with a file; view scope uses
the view's files. Existing notes are never overwritten (conflicts are
reported). An optional saved-view index note is a follow-up.

## 7. Slices and sequencing

| Slice | Contents | Gate |
|---|---|---|
| 5a | JSON sidecar export/import, atomic auto-write on shutdown, Settings actions | Phase 4 |
| 5b | Symlink-tree export with dry-run, manifest, Windows fallback | 5a |
| 5c | Finder tag export (macOS, opt-in) | 5b |
| 5d | Obsidian vault export | 5a; independent of 5b/c |

## 8. Acceptance criteria

- Export → rebuild the DB and re-scan → import restores manual assignments,
  concepts, aliases, merges, and saved views with zero LLM calls.
- A second import is a no-op.
- Unknown content hashes and unresolvable merge sides are reported, not fatal.
- Shutdown writes an atomic sidecar; a write failure never blocks quitting.
- Symlink export never deletes files it did not create.
- FAT/exFAT targets are refused with an explanation.

## 9. Decisions

- **D13 — Sidecar location.** Configurable `CABINET_CURATION_PATH`, default
  next to the DB; per-source `.cabinet/` is a later opt-in.
- **D14 — Restore policy.** Explicit import only; no silent auto-restore.
- **D15 — Sidecar contents.** Concepts/aliases/merges/manual assignments/views;
  per-instance assignments and full facts are out of scope for 5a.
- **D16 — Export scope.** Library-wide for the sidecar; view/tree scope for
  the symlink and vault exports.
- **D17 — Symlink safety.** Manifest-tracked generated links only; never
  touch unrelated files.

## 10. Non-goals

- Moving or rewriting source files.
- RAG/answer synthesis over the vault (Tier 3).
- Two-way sync with Finder tags or Obsidian in 5a–5d.
- Cloud sync or multi-user curation.
