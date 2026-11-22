# Design: Phase 1 (content identity + folder watching) and Phase 2 (facts cache)

- **Status:** Draft for review — Phase 1a/1b and Phase 2a implemented.
  Decisions D1–D4 and D7 settled 2026-11-22 (§7); D5–D6 remain open
- **Date:** 2026-11-22
- **Scope:** Phase 1a content-based file identity, Phase 1b folder watching,
  Phase 2 per-content facts cache (summary / entities / embeddings / optional
  full-text index)
- **Tracked in:** [ROADMAP.md](../ROADMAP.md), which also covers the later
  phases this design deliberately does not solve (Phase 3 concept governance,
  Phase 4 views/dynamic taxonomies, Phase 5 projecting the view outward)
- **Supersedes/extends:** the taxonomy-governance and projection notes already
  in the roadmap; this doc adds the identity, watching, and derived-facts
  layers underneath them

## 1. Problem statement

Cabinet is a *view* over files on disk: it extracts text, asks an LLM to
assign hierarchical categories, and presents virtual folders without moving
anything. That model is sound, but three properties of the current
implementation make it fragile and expensive in exactly the situations a
real document pile produces:

1. **Files are identified by path, not content.** In
   `backend/src/cabinet/services/scanner.py`, reconciliation keys everything
   on `(source_id, path)`. A rename or move done in Finder/Explorer is seen
   as "old file missing, new file pending": the new row is re-extracted and
   re-sent to the LLM, and the old row's category assignments — including
   manual ones, the user's own curation — are stranded. The
   `files.content_hash` column exists but is only populated when a file
   changes *in place*; files are never hashed on first scan, so moves can
   never be recognised. Consequence: the cost of a user restructuring their
   folders is proportional to the restructure, paid in LLM tokens.

2. **Nothing watches the disk.** The view only refreshes when the user
   presses sync. A view that silently goes stale undermines trust in the
   view. Watching naively on top of (1) would be worse: every external
   rename would auto-trigger a paid re-categorisation. Watching is only
   safe once identity is content-based — hence the phase ordering.

3. **Extraction work is thrown away.** Extracted text is held in a
   `dict[int, str]` for one pipeline run and discarded; categorisation sees
   only the first 2,000 characters (`_FILE_TEXT_PREVIEW_CHARS` in
   `backend/src/cabinet/services/categorization.py`). Every retry, move, or
   taxonomy change pays full extraction + LLM cost again, long documents are
   judged by their opening page, and full-text search is impossible. This
   ephemerality was a deliberate privacy decision (an earlier schema's FTS5
   table and text columns are actively dropped by
   `backend/src/cabinet/db/engine.py`), so Phase 2 reverses it *narrowly,
   in explicit storage tiers* — see §5.4 (D3).

## 2. Design principles

These govern both phases and the later ones:

1. **Disk is truth, the database is a cache.** Any DB state must be
   rebuildable by re-scanning. Reconciliation, not event handling, is the
   mechanism that converges DB → disk.
2. **Identity is content, location is an attribute.** A document is its
   bytes (content hash). Path, filename, and source are mutable attributes
   of a sighting of that content.
3. **Human curation is irreplaceable; AI output is disposable.** Manual
   assignments must survive moves, renames, and re-runs (they already
   survive re-runs). Anything an LLM produced can be regenerated, so it
   should be *cached against the content hash*, never re-derived needlessly.
4. **Watcher events are hints, never truth.** Events may be missed (app
   closed, queue overflow, network filesystem). The watcher only schedules
   the existing idempotent reconcile sooner; correctness never depends on it.
5. **Derived data is versioned.** Every cached fact records what produced it
   (extractor version, prompt/facts version, embedding model) so staleness
   is detectable and refresh is incremental.
6. **Storing document-derived data is tiered and explicit (D3, decided).**
   Tier 0 metadata+hash and Tier 1 facts (summary/entities/one embedding)
   are the default posture; Tier 2 stored full text is per-source opt-in;
   Tier 3 chunked embeddings for Q&A are out of scope. Every tier above 0
   has a working purge path (§5.4).
7. **Facts find documents; originals remain the source.** Stored facts are
   a catalogue card, not a copy of the book. Facts carry provenance
   locators (page, sheet) where extractors can supply them, and viewing
   content always means opening the original file in its proper tool.

## 3. Phase 1a — Content-based identity

### 3.1 The document / instance split (D2, decided)

One document, many locations. Identity is split into two levels, because
categories, facts, and extraction cost belong to *content*, while paths
belong to *sightings* of that content:

- **Document** — the content itself, identified by content hash. Facts
  (Phase 2) and category assignments live here. A document is extracted and
  categorised **once**, however many copies of it exist.
- **Instance** — one place a document exists: source, path, filename,
  inode/device, size, mtime. "Open file" opens any live instance; the
  details panel lists all of them.

This supersedes the earlier "copy assignments to duplicate rows" idea: an
exact duplicate does not get categories copied, it *is* the same document —
manual categories included, no per-copy sync logic. Location-specific human
filing (the same blank form deliberately filed differently under two
clients) is supported as optional **per-instance manual additions** layered
on the document's categories; a document's effective categories at a given
location are the union. Category counts and drift metrics count
*documents*, not instances.

Physical schema (new tables via `create_all`, new columns via the existing
`_add_missing_columns` pattern in `backend/src/cabinet/db/engine.py`; the
existing `files` table becomes the instance table and keeps its name to
limit churn):

| Change | Purpose |
|---|---|
| New table `documents`: `id`, `content_hash` (unique, nullable while unhashed), `status`, `cluster_id` (nullable, §3.4), timestamps | content identity; pipeline status moves here from `files` |
| `files.document_id` → `documents.id` (index) | instance → document |
| `files.device_id`, `files.inode` (nullable; index `(source_id, device_id, inode)`) | `st_dev`/`st_ino` at last sighting (on Windows, Python derives `st_ino` from the NTFS file ID) for zero-read rename detection |
| New table `document_categories` (`document_id`, `category_id`, `confidence`, `is_manual`), same shape as today's `file_categories` | assignments at document level |
| Existing `file_categories` retained, repurposed: per-instance manual additions only (`is_manual` always true for new rows) | location-specific filing without forking the document |

`content_hash` values gain an algorithm prefix (`sha256:<hex>`); bare hex
from older rows is treated as sha256. Hashing cost control:

- Skip hashing when path + size + mtime all match the instance row
  (today's fast path — unchanged behaviour).
- Hash any path that is new or changed, and lazily backfill `NULL` hashes
  during quick-sync (each scan backfills what it touches; no big-bang
  hashing job). Until hashed, a document is *provisional*: one instance,
  `content_hash = NULL`. When backfill later reveals two provisional
  documents share a hash, they merge (instances repoint; assignment
  conflicts resolve manual-beats-AI, then higher confidence).
- Raise the read chunk in `backend/src/cabinet/utils/hashing.py` from 8 KiB
  to 1 MiB — the current size is syscall-bound on large files.
- Migration order: create `documents`/`document_categories` → one document
  per existing `files` row → move assignments across → hash backfill and
  merges happen lazily through normal scans. No LLM or extraction re-runs
  are triggered by the migration itself.

### 3.2 Reconciliation algorithm (replaces path-only matching)

For each source scan, after walking the filesystem (unchanged, including
exclusion rules), resolve each sighting in this order:

1. **Exact path match** — same instance. Size+mtime unchanged → done.
   Changed → rehash: same hash → touch-only update; new hash → the
   instance repoints to the (new or existing) document for that hash and
   that document goes back to `pending` if it has no current assignments;
   the old document keeps any other instances, or becomes an orphan
   tombstone (§7, D6).
2. **Inode match** — `(device_id, inode)` matches an instance in the same
   source whose path is no longer on disk, **corroborated by size and
   mtime** (§3.3): a rename/move within the volume. Update the instance's
   `path`/`filename` in place; the document — and therefore status, facts,
   and all categories — is untouched. Cost: zero reads, zero LLM.
3. **Hash match to an existing document** — hash the sighting and attach it
   to that document. Two sub-cases, same mechanism:
   - the document has a `missing` instance in this source and the pairing
     is unambiguous → it was a move (possibly cross-volume): update that
     instance's path instead of creating a new one;
   - otherwise (live instances exist, here or under another source) → it
     is a copy: create a new instance of the same document. No extraction,
     no LLM, categories already correct by construction (D2).
   Ambiguous pairings (several identical missing instances) attach as a
   copy rather than guessing which one moved.
4. **Genuinely new content** — create a new document (`pending`) plus its
   first instance.

Instances whose paths vanish and match nothing become `missing` (current
behaviour) and are **retained** as tombstones, because a later scan may
match them via step 3 (drive remounted, folder moved back); retention/GC
policy is D6 (§7). A missing source root must never mass-mark instances
missing: `scan_source` already raises when the root is not a directory,
and that guard is load-bearing for watching (§4.4).

### 3.3 Filesystem capability ladder (D7, decided)

Inode quality varies by filesystem, so identity degrades in steps rather
than trusting one mechanism everywhere — and never hashes speculatively,
because hashing everything means reading the whole corpus every scan
(slow, battery, and harmful on cloud-backed folders, §4.2):

1. **APFS / NTFS / ext4 and similar:** inode match corroborated by
   size+mtime (§3.2 step 2) — renames cost zero content reads. An inode
   match that fails corroboration falls through to hashing.
2. **FAT / exFAT (removable drives, some externals):** inode can be zero
   or unstable, so it is ignored. Identity is path + size + mtime, with
   hashing only for new/changed paths (a move then costs one read of that
   file). The source row shows a small "limited move detection on this
   volume" note — informational, never blocking.
3. **Unknown/network filesystems:** treated as tier 2 until observed
   otherwise. Filesystem type is detected per source root at scan time
   and recorded for display; detection failure means tier 2.

### 3.4 Near-duplicates and version clusters (deferred to 2b/3)

Exact-hash identity does nothing for poor-man's version control
(`report.docx`, `report_v2.docx`, `report_FINAL.docx`): one changed byte
is a different document. Left alone, versions multiply extraction/LLM
cost, inflate category counts, and clutter the view. Planned handling —
**grouped, never merged** (nothing hidden, every version stays openable):

- **Detection, cheapest first:** a fuzzy fingerprint (MinHash/SimHash over
  word shingles of extracted text, stored per document with facts) finds
  near-duplicate candidates; filename-stem similarity and same-folder
  proximity corroborate. Embedding cosine is a *secondary* signal only —
  it measures topic similarity, so different invoices from one supplier
  score like versions of each other; that makes it right for "similar
  documents" and wrong as the primary version detector.
- **Model:** `documents.cluster_id` (§3.1 reserves the column) groups
  versions; the cluster has a primary (newest by mtime unless the user
  picks another). The browse view shows "Report — 4 versions"; category
  counts and drift metrics count a cluster once.
- **Cost:** a new version inherits the cluster's categories as a
  *proposal* the categoriser confirms or adjusts over its summary, instead
  of a cold start. Facts are still per-document (versions genuinely
  differ), but the LLM spend per version drops.

Job accounting gains a `moved_count` so the UI can say "212 files moved,
0 re-categorised" — the user-visible proof this phase worked.

### 3.5 Acceptance criteria

- Renaming or moving a categorised file externally, then syncing, preserves
  its categories (AI and manual) and makes **zero** LLM/extraction calls.
- Copying a categorised file to a second location creates a second instance
  of the same document — same categories and facts, no duplicate document,
  no LLM/extraction calls.
- Restructuring 1,000 files within a source on a tier-1 filesystem costs
  one walk plus stat calls — no content reads, no LLM calls. On FAT/exFAT
  it costs one hash read per moved file, still no LLM calls.
- Cross-volume move costs one hash read per moved file, no LLM calls.
- Editing a file's content re-queues only that document.
- Migrating an existing database produces one provisional document per file
  with assignments intact, and backfill merges true duplicates without
  losing manual assignments.
- New scanner tests: rename, move to subfolder, copy (duplicate content,
  incl. across sources), in-place edit, content replaced at same path,
  ambiguous identical-hash missing instances, missing-then-returned file,
  inode match rejected on failed corroboration, provisional-document merge.

## 4. Phase 1b — Folder watching

Goal: the view converges on its own while Cabinet is running, without the
watcher ever becoming a second source of truth or a cost amplifier. This
phase must not ship before §3; watching path-identified files would turn
every external rename into paid LLM work.

### 4.1 Mechanism

- Add `watchdog` (FSEvents / inotify / ReadDirectoryChangesW) as a backend
  dependency. Verify it bundles cleanly in the PyInstaller build
  (`backend/cabinet-api.spec`) on all three platforms before committing to
  it; fallback is `watchdog`'s polling observer.
- A new `WatcherService` (`backend/src/cabinet/services/watcher.py`) runs
  inside the FastAPI lifespan in `backend/src/cabinet/app.py`. It owns one
  observer and one watch per watch-enabled source. Starting/stopping tracks
  the backend process lifetime, which Electron's main process already
  supervises (`src/main/backend.ts`). Cabinet is not a background daemon in
  this phase: changes made while the app is closed are picked up by
  reconcile-on-launch (§4.3).
- The event handler does almost nothing: it records "source N is dirty" in
  a set and (re)arms a debounce timer. All filesystem interpretation stays
  in the existing scanner/reconcile path.

### 4.2 Scheduling rules

- **Debounce:** fire after 3 s of quiet per source, with a 15 s maximum
  wait, so a long copy doesn't trigger a scan per file and a busy folder
  still syncs eventually. Both values configurable via `CABINET_WATCH_*`
  settings in `backend/src/cabinet/config.py`.
- **Single-flight per source:** never run two scan+pipeline jobs for one
  source concurrently. If events arrive during a run, set a "dirty again"
  flag and run exactly one follow-up pass.
- **Reuse the job system:** watcher-triggered runs create normal `Job` rows
  so progress, errors, and cancellation work through the existing SSE
  channel. Add a `trigger` field (`manual` | `watch` | `launch` |
  `periodic`) so support questions ("why did it sync?") are answerable.
- **Stability check:** editors and browsers write via temp-file-then-rename
  and partial downloads (`.crdownload`, `.part`, `~$` Office locks). The
  scanner already skips `~$` and hidden names; the watcher additionally
  ignores known temp suffixes, and reconcile's size+mtime+hash checks mean
  a half-written file either doesn't hash-match yet or is re-queued when it
  settles. A file whose size changes between two stats 1 s apart is
  deferred to the next pass.
- **Cloud placeholder files** (iCloud/OneDrive dataless stubs): the file
  *appears* present with full metadata but its bytes are not local, and
  reading it forces a download. V1 mitigation is conservative: hashing only
  happens in reconcile outcomes §3.2 steps 1/3, never speculatively on raw
  events. Detected placeholders get instance availability
  `cloud_placeholder` (§4.4): shown with a cloud badge, hashing/extraction
  deferred until the file is hydrated or the user explicitly opens or
  processes it. **Resolved (D4):** detection is a metadata-only `stat` —
  macOS `SF_DATALESS` in `st_flags`, Windows `FILE_ATTRIBUTE_OFFLINE` /
  `RECALL_ON_OPEN` / `RECALL_ON_DATA_ACCESS` in `st_file_attributes`
  (`utils/cloud.py`). The scan caches the flag on the instance; the scanner
  and pipeline both park placeholders in an instance-only `deferred` status
  and never read their bytes. Linux/BSD expose no portable marker and
  report "not a placeholder" rather than guessing.

### 4.3 Safety nets (correctness without events)

Watcher events are lossy by nature, so convergence cannot depend on them:

1. **Reconcile-on-launch** for every watch-enabled source (cheap after §3:
   a walk + stats when nothing changed).
2. **Periodic reconcile** every `CABINET_WATCH_POLL_INTERVAL` (proposed
   default 30 min) for watch-enabled sources — also the mechanism for
   filesystems where native events are unreliable (some network mounts),
   where the polling observer or pure periodic scans are used instead.
3. Manual sync remains available and unchanged.

### 4.4 Data and API changes; availability states

- `sources.watch_enabled` (Boolean, additive migration). **Decided (D1):**
  off for existing sources, offered at source creation, and the creation
  default flips to on only after 1a has soaked in a release. A global
  `CABINET_WATCH_ENABLED` kill switch overrides per-source flags.
- Exposed through the existing source create/update schemas and the Sources
  tab (a watch toggle + "last synced / watching / paused / unavailable"
  state per row). Deleting or disabling a source removes its watch.
- **Removable drives:** if a source root disappears (drive unmounted), the
  source becomes *unavailable*: the watcher stops scheduling for it, scans
  are skipped (never interpreted as "all files deleted" — the guard in
  §3.2), and the view keeps showing its documents with instances greyed,
  browse/search still working over stored facts, "open" disabled with a
  "drive not connected" explanation. To survive remounting at a different
  mount point (drive letter change, `/Volumes/Name-1`), sources also record
  `volume_id` (volume UUID/serial where the OS exposes one) and
  `volume_rel_path` (source root relative to the volume root); on launch, a
  source whose recorded path is absent is rebound via volume + relative
  path before being declared unavailable. Volume enumeration per platform
  is an implementation spike, not a design unknown.
- **Instance availability** is reported separately from pipeline `status`
  (which lives on the document after §3.1): `available`,
  `source_unavailable`, `cloud_placeholder`, `missing`. Only `missing` is
  persisted as a state change by reconcile; the rest are derived at read
  time from source state plus the placeholder flag the scan records, so an
  unplugged drive never writes thousands of row updates. Same greyed visual
  language for disconnected and cloud files, different underlying cause.

### 4.5 Acceptance criteria

- Dropping a new document into a watched folder makes it appear,
  categorised, with no user action (given a configured LLM).
- A 5,000-file external restructure produces one coalesced job, not 5,000,
  and zero LLM calls for content-identical moves (§3 working as designed).
- Killing event delivery (observer stopped) still converges via launch and
  periodic reconcile.
- Unmounting a watched drive loses no category assignments, its documents
  remain browsable/searchable as unavailable, and remounting it — including
  at a different mount point — restores it without a re-scan storm or any
  LLM calls.
- Tests: fake-observer unit tests for debounce/single-flight/dirty-again;
  tmp-dir integration tests for create/modify/rename/delete convergence,
  source-unavailable transitions, and volume rebinding.

## 5. Phase 2 — Facts cache (derived data per content hash)

Goal: pay extraction and content-understanding cost **once per document
content**, then let categorisation, search, and later taxonomy/view work
reuse the result as database lookups. This is the layer that makes stronger
categorisation and (in later phases) multiple dynamic taxonomies cheap:
reorganising becomes re-querying stored facts, not re-reading files or
re-prompting over raw text.

### 5.1 New table: `content_facts`

One row per **document** (§3.1) — 1:1 with `documents` via the content
hash, kept in a separate table so Phase 1's document rows stay lean and
facts can be purged independently (§5.4). Duplicates and moved files share
the row by construction, since they are instances of the same document:

| Column | Type | Notes |
|---|---|---|
| `content_hash` | String, PK | `sha256:<hex>` |
| `extractor_version` | String | bumped when any extractor's output changes meaningfully |
| `facts_version` | Integer | version of the summarisation/entity prompt + parser |
| `summary` | Text, nullable | ~1–2k chars; the primary input to categorisation |
| `doc_type` | String, nullable | invoice, contract, letter, paper, spreadsheet… (free text in this phase; governed in Phase 3) |
| `language` | String, nullable | BCP-47-ish code if cheaply derivable |
| `entities` | JSON, nullable | `{people: [], organisations: [], dates: [], amounts: []}` — best-effort, schema-tolerant |
| `topics` | JSON, nullable | flat keyword list from the facts pass |
| `embedding` | BLOB, nullable | little-endian float32 (float16 later if size matters) |
| `embedding_model` | String, nullable | e.g. `nomic-embed-text`, `text-embedding-3-small` |
| `embedding_dim` | Integer, nullable | guards against silent model swaps |
| `char_count`, `page_count` | Integer, nullable | from extractor metadata |
| `created_at`, `updated_at` | DateTime | |

**Implemented (2a):** the table ships without the embedding columns; 2b
adds them. Facts carry a `provenance` JSON (page/sheet counts, headings,
sheet names, sampled-vs-source char counts) so a citation can be traced
back to the original. Exact duplicates share a row by hash.

Orphaned facts (their document has no live instances) are kept for a grace
period aligned with tombstone retention (§7, D6), then garbage-collected
by a maintenance step in the periodic reconcile.

### 5.2 Pipeline changes

`PipelineService.process_pending_files` currently extracts a batch, holds
texts in memory, categorises, discards. New flow per batch:

1. Work per pending **document** (status now lives on `documents`, §3.1;
   hash guaranteed present by §3 for anything pending — provisional
   unhashed documents wait for backfill).
2. For each hash, facts are **fresh** if a `content_facts` row exists with
   matching `extractor_version`, `facts_version`, and (when embeddings are
   enabled) `embedding_model`/`embedding_dim`. Fresh → no extraction, no
   facts LLM call.
3. Stale or missing → extract once for the document (any one live
   instance's path), then run a `FactsService`:
   - **Summary/entities/topics:** structure-aware input instead of "first N
     chars" — title/headings where the extractor provides them (PDF via
     pymupdf4llm markdown headings, DOCX styles, XLSX sheet names), plus
     head, sampled middle, and tail segments within a token budget.
     Map-reduce (chunk summaries → final summary) only above a configurable
     length threshold; most documents fit the single-pass budget.
   - **Embedding:** computed from summary + topics (+ opening text), not
     raw full text — cheaper and usually more topical.
4. Categorisation consumes `summary` + `entities` + `doc_type` per document
   instead of the 2,000-char raw snippet (the snippet path remains as
   fallback when facts are disabled for a source, §5.4), and writes to
   `document_categories`. Prompt format and category-resolution logic are
   otherwise unchanged in this phase — candidate-concept retrieval using
   embeddings is Phase 3 work.
5. Commit boundaries stay per-batch, preserving the current
   crash-loses-one-batch property. A facts failure marks only that document
   for retry, mirroring today's `extracted` retry semantics.

Embeddings need a provider capability the current `llm/` layer lacks. Add
an `embed(texts) -> list[list[float]]` method to the provider interface:
Ollama, OpenAI, and Gemini support it; **Anthropic has no embeddings API**,
so an Anthropic chat config needs a separately configured embedding model
(default suggestion: local Ollama `nomic-embed-text`). Split Phase 2 into
2a (summary/entities/topics — works with every existing provider) and 2b
(embeddings + the new provider capability) so 2a isn't blocked on it.

**Scope guard — embeddings here are for organisation, not RAG.** One vector
per document supports candidate-concept retrieval (Phase 3), "similar
documents", version-cluster corroboration (§3.4), and semantic *ranking* of
search results. It is a lossy numeric fingerprint of meaning: closeness is
measurable (cosine), the document is not reconstructable from it, and it is
not a copy. It would make a poor retrieval layer for pinpoint Q&A inside
long documents — that (chunking, per-chunk vectors + stored chunk text,
answer synthesis) is a retrieval-augmented-generation product feature and
Tier 3 storage (§5.4), explicitly a later-phase decision, not something
Phase 2 drifts into. No dedicated vector database: at personal scale
(≤ ~100k document vectors), brute-force cosine over SQLite BLOBs — or the
`sqlite-vec` extension if in-SQL search is wanted — is tens of
milliseconds, and a separate vector store would add a heavy, hard-to-bundle
dependency to an Electron+PyInstaller app while breaking the single-file
local-database story.

### 5.3 Search (minimal, enabling only)

This phase does not redesign search, but facts make two cheap wins possible
without the client loading every file:

- **Server-side text search** over `summary`/`topics`/filename via a new
  `GET /files?q=` filter (SQL `LIKE` is fine at this scale; FTS5 over
  summaries if ranking is wanted).
- **Opt-in full-text index:** an FTS5 virtual table over extracted text,
  keyed by hash, populated only for sources with `store_text_index`
  enabled (§5.4). This consciously re-introduces what `_cleanup_legacy_fts`
  removed — under an explicit per-source flag, with purge support, not as
  a global default.

### 5.4 Privacy and storage posture — tiered storage (D3, decided)

Today's promise — "text is never stored" — is documented in `CLAUDE.md`
and the README, so changing it is a product decision, not an implementation
detail. The fear to design against is the database quietly becoming a
second copy of the filesystem. Storage is therefore tiered, with each tier
explicit about what it holds and what it costs:

| Tier | Stored per unique document | Size (per 10k documents) | Default |
|---|---|---|---|
| 0 — Metadata + hash | filename/path/size/mtime/hash/document row | ~hundreds of bytes each (~few MB) | always |
| 1 — Facts | summary + entities + topics + provenance ≈ 2–3 KB, one embedding 3 KB (768-d float32; 1.5 KB float16), fuzzy fingerprint | ~50–60 MB | **on for new sources** (`store_facts`), off for migrated sources until opted in |
| 2 — Full-text index | extracted text in FTS5, capped by `extraction_max_chars` (50k chars) | up to ~500 MB | **off everywhere** (`store_text_index` opt-in per source) |
| 3 — Chunked embeddings + chunk text (RAG Q&A) | tens of vectors and text chunks per document | largest; approaches duplication | **out of scope** for Phases 1–2 (§5.2 scope guard) |

Read on proportions: against a pile of PDFs averaging a few hundred KB,
Tier 1 is roughly 2–3% overhead — a catalogue card, not a photocopy.
Tier 2 stays a minority for binary documents (extracted text ≪ PDF size)
but approaches a genuine second copy for plain-text corpora, which is why
it is opt-in. Two guardrails keep Tier 1 honest: a summary is never stored
longer than its source text, and embeddings may be skipped for very small
files where a 3 KB vector dwarfs a 1 KB note. Exact duplicates are free at
every tier because facts key on the document/hash (§3.1).

Rules:

- Per-source `store_facts` flag (Tier 1), with plain-settings copy
  explaining that AI-generated summaries of documents will be kept in the
  local database, so documents can be found and understood; viewing
  content always opens the original file in its proper tool (principle 7).
- Per-source `store_text_index` flag (Tier 2), default off everywhere —
  the materially more sensitive store.
- Turning a flag off purges derived rows: facts are shared per document,
  so purge deletes facts whose documents are no longer referenced by any
  facts-enabled source; FTS rows delete immediately. With facts disabled a
  source runs in today's ephemeral mode (snippet categorisation, 2,000
  chars, nothing persisted).
- The DB becomes content-bearing for backup purposes once Tier 1 is on;
  Settings should say so next to the toggle.
- README/`CLAUDE.md` wording updates land **with the implementation**, not
  with this draft.

### 5.5 Acceptance criteria

- Re-running full reindex with fresh facts performs no extraction and no
  facts LLM calls; only categorisation runs.
- A file that moves after categorisation reuses facts *and* categories
  (§3 + §5 composing: zero LLM, zero extraction).
- A document whose topic is only clear at the end (e.g. signature block /
  totals page) is categorised from its summary, demonstrably better than
  the first-2,000-chars baseline on a small fixture set.
- Disabling `store_facts` for a source purges its derived data and the app
  continues in today's ephemeral mode for that source.
- A fact's provenance locator opens the original at the cited page/sheet.
- Tests: facts freshness/version invalidation, per-document dedupe
  (multiple instances extract once), facts-failure retry isolation, purge
  behaviour, embedding model/dim mismatch treated as stale.

## 6. Sequencing, migration, and rollback

Each slice is independently shippable and valuable; do not bundle:

| Slice | Contents | User-visible payoff | Gate |
|---|---|---|---|
| 1a | §3 identity: document/instance split, inode+hash reconcile, capability ladder, `moved_count` | renames/moves/copies free; restructures stop costing LLM tokens; duplicates indexed once | — |
| 1b | §4 watching (dependency, service, toggle, job trigger, availability states, volume rebinding) | view stays current while app runs; disconnected drives shown honestly | 1a shipped |
| 2a | §5.1–5.2 minus embeddings; Tier 1 `store_facts` flag; fuzzy fingerprint | whole-document categorisation; reindex reuses facts | 1a (hash guarantee) |
| 2b | provider `embed()`, embedding storage, §5.3 search filters, opt-in Tier 2 FTS, version-cluster detection (§3.4) | semantic groundwork for Phase 3; server-side search; versions grouped | 2a |

Migration is additive in structure (new columns via `_add_missing_columns`,
new tables via `create_all`, FTS table created lazily only when a source
enables Tier 2) with one deliberate data move: 1a creates documents and
transfers assignments to `document_categories` (§3.1), after which only lazy
hash backfill, document merges, and instance path updates rewrite rows.
Rollback = disable flags / kill switch; orphaned columns and the facts
table are inert if the code is reverted (facts can be dropped by purge).
Reverting *past* the 1a assignment transfer is not supported — back up the
DB before first run of a 1a build (the migration should do this itself).

## 7. Decisions

### Decided 2026-11-22

- **D1 — Watch default.** Off for existing sources; offered at source
  creation; the creation default flips to on only after 1a has soaked in a
  release (§4.4).
- **D2 — Duplicates and manual categories.** Superseded by the
  document/instance split (§3.1): identical content is one document with
  many instances, so AI and manual categories are shared by construction —
  indexed once, no copying. Location-specific filing is per-instance manual
  additions. Near-duplicates/versions are grouped, not merged (§3.4).
- **D3 — Stored derived data.** Tiered storage (§5.4): Tier 1 facts on for
  new sources with clear Settings copy, Tier 2 full-text opt-in per source,
  Tier 3 (RAG chunking) out of scope. Facts carry provenance locators;
  originals are always opened in their own tool (principles 6–7).
- **D7 — Inode reliability.** Capability ladder (§3.3): inode is a hint
  requiring size+mtime corroboration on capable filesystems, ignored on
  FAT/exFAT (hash on new/changed paths instead, with an informational
  source note). Removable sources additionally record volume identity +
  relative path and degrade to an honest "unavailable" state when
  disconnected (§4.4) — never mass-missing.
- **D4 — Cloud placeholders.** Detect with a metadata-only `stat` before
  any read: macOS `SF_DATALESS` (`st_flags`), Windows `FILE_ATTRIBUTE_OFFLINE`
  / `RECALL_ON_OPEN` / `RECALL_ON_DATA_ACCESS` (`st_file_attributes`).
  Linux/BSD have no portable marker, so they report "not a placeholder"
  rather than guessing (no zero-block heuristic — it false-positives on
  sparse and network filesystems). The scan caches `files.is_placeholder`;
  unhashed placeholders get the instance-only `deferred` status and are
  never hashed or extracted, hydration re-queues them, and the API reports
  `cloud_placeholder` availability (§4.2, §4.4).

### Still open

- **D5 — Embedding provider for Anthropic configs.** Anthropic has no
  embeddings API, so 2b needs an embedding source for Anthropic users:
  separate embedding config defaulting to local Ollama (proposed, §5.2), a
  second cloud key, or deferring 2b embeddings for Anthropic-only users.
  Note the embeddings in question are document-level *organisation* vectors
  in SQLite, not a RAG/vector-database commitment (§5.2 scope guard).
  Blocks 2b only.
- **D6 — Tombstone retention.** `missing` instances and orphaned
  documents/facts are retained so moved-away-and-back files and unplugged
  drives restore with curation intact; they are hidden from normal browsing
  (a user-deleted file disappears from the view immediately — the tombstone
  is a behind-the-scenes safety net, not a visible ghost). Open part: how
  long before GC, if ever? Proposal: indefinite, with a Settings "purge
  missing files (n)" action showing count/size, since tombstones are small
  and carry manual curation. Does not block 1a as long as tombstones are
  hidden from browse/search results from day one.

## 8. Risks and non-goals

- **Hashing I/O on first upgraded scan.** Lazy backfill bounds this, but a
  source of multi-GB files will still feel it. Mitigate with job progress
  that distinguishes "hashing for identity" from "categorising".
- **Watcher battery/CPU on huge trees.** Debounce + single-flight bound the
  damage; the periodic interval is the floor, events only improve latency.
- **PyInstaller + watchdog native parts** are the main build risk in 1b;
  spike the bundled build first.
- **Facts quality is model-quality.** A bad summary now poisons
  categorisation durably instead of transiently. Mitigations: facts
  versioning (§5.1), provenance locators so a suspect fact can be checked
  against the original in one click (§5.1), details-panel visibility of the
  summary, and a per-document "regenerate facts" action (cheap to add once
  facts exist).
- **Document-merge mistakes (1a backfill).** Provisional documents merge
  only on exact full-content hash equality (§3.1) — no fuzzy merging ever;
  fuzzy similarity creates clusters (§3.4), which cannot lose assignments.
  The migration must back up the DB before the assignment transfer (§6).
- **Non-goals for Phases 1–2:** taxonomy governance concepts/aliases
  (Phase 3), multiple views or per-user taxonomies (Phase 4), exporting the
  view to the OS or an Obsidian vault (Phase 5), RAG-style Q&A over the
  pile and any dedicated vector database (Tier 3, §5.4 — a later-phase
  product decision if ever), OCR for scanned PDFs, new extractors
  (e.g. PPTX), OS-keychain storage for API keys, and running as a
  background daemon while the app is closed. All are tracked in
  [ROADMAP.md](../ROADMAP.md).

## Appendix A — Why this isn't already solved by Obsidian plugins

Checked 2026-11-22 against the current plugin field. Closely adjacent
tools exist, but they all operate *inside a vault, on markdown notes*:

- **Auto-tagging/frontmatter:** AI Tagger Universe (batch vault tagging,
  nested tags, many providers incl. Ollama), AI Tagger, Auto Tag, LLM
  Tagger, Metadata Auto Classifier (tags + custom frontmatter fields).
  These write tags *into `.md` note files* the user authored.
- **Inbox automation:** Note Companion (formerly AI File Organizer 2000)
  suggests folder/tag/title and **moves and renames files** within the
  vault; also annotates images and transcribes audio. Closest in spirit,
  but destructive, single-folder placement, vault-internal.
- **Semantic discovery:** Smart Connections (local embeddings, related
  notes) — explicitly no tagging/organisation; Copilot for Obsidian and
  Text Generator cover chat/RAG/template generation, not dedicated
  organisation of an arbitrary document pile.
- **Attachment housekeeping:** Vaultkeeper, Vault Reorganizer, Attachment
  Organizer — rename/relocate attachments around notes, some OCR.

The gap Cabinet occupies, and which Phases 1–2 deepen:

1. **The pile outside the vault:** arbitrary existing folders of PDF, DOCX,
   XLSX, and code/text that Obsidian treats as opaque attachments, with no
   import or conversion step.
2. **Non-destructive, multi-membership virtual folders** instead of moving
   files into one folder or editing metadata into the files.
3. **A job-based pipeline** (scan → extract → categorise, progress, cancel,
   now identity-aware and optionally watched) rather than per-note commands.

Positioning consequence: Cabinet is the *ingestion and first-pass curation
layer* for documents-in-the-wild — complementary to Obsidian rather than a
competitor. Phase 5 (vault export: one markdown stub per document with
facts as frontmatter and concepts as tags) would make that relationship
explicit instead of forcing users to choose.
es.
3. **A job-based pipeline** (scan → extract → categorise, progress, cancel,
   now identity-aware and optionally watched) rather than per-note commands.

Positioning consequence: Cabinet is the *ingestion and first-pass curation
layer* for documents-in-the-wild — complementary to Obsidian rather than a
competitor. Phase 5 (vault export: one markdown stub per document with
facts as frontmatter and concepts as tags) would make that relationship
explicit instead of forcing users to choose.
ad of forcing users to choose.
