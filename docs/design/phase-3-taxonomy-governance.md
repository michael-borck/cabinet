# Design: Phase 3 — Taxonomy governance

- **Status:** 3a–3b implemented (schema, aliases, provisional concepts,
  resolution order, concept-embedding backfill; candidate-ID assignment,
  learning examples, auto-promotion, review inbox). 3c–3d pending.
- **Date:** 2026-11-23
- **Scope:** Govern the *vocabulary* of the category tree: stable concepts,
  aliases, provisional concepts, a human review inbox, consolidation, and a
  learning loop from manual corrections. Builds on Phase 1 (identity) and
  Phase 2 (facts + embeddings).
- **Related:** [Phase 1+2 design](phase-1-2-identity-watching-facts.md),
  [ROADMAP](../ROADMAP.md).

## 1. The problem

The category *structure* is bounded — it is a tree, so it cannot become a
graph hairball. The *vocabulary* is not:

- **Near-duplicate synonyms.** The LLM sees the tree in its prompt but
  nothing forces reuse: "Finance" vs "Finances", "Invoices" vs "Billing",
  "Contracts" vs "Agreements".
- **Path dependence.** Files are categorised in batches, so the first
  batches define the taxonomy and later files bend to fit it.
- **Singleton sprawl.** Many categories hold exactly one document, which
  makes the tree look precise but useless for browsing.
- **No memory of corrections.** A user re-filing "Acme invoice" is not
  remembered; the next Acme invoice starts cold.

Goal: **category count should grow with content diversity, not file count**,
and the tree should become more *personal* over time without the user
hand-maintaining it.

## 2. Principles

1. **Bounded structure, unbounded vocabulary.** Keep the tree as the
   structure; make names, aliases, and definitions the flexible layer.
2. **Grouped, never merged, until a human says so.** AI may *propose*
   merges; only an approved merge changes assignments.
3. **Pure DB updates.** A rename or merge is an alias/definition change and
   an assignment repoint — never a re-extraction or LLM re-run.
4. **Prefer reuse.** Constrain the LLM to pick existing concept IDs before
   it may invent a name.
5. **Learn from the user.** Manual corrections are first-class training
   examples, not one-off edits.
6. **Explainable.** Every AI assignment can show the concept, confidence,
   and (later) the example that influenced it.

## 3. Data model

Phase 3 evolves the existing `categories` table rather than introducing a
parallel `concepts` table: every current assignment already points at a
category, and keeping one identity avoids a migration that can lose
curation. A category *is* a concept.

### 3.1 `categories` (extended)

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | unchanged |
| `name` | String | canonical display name |
| `slug` | String | canonical slug, unique per parent (existing rule) |
| `parent_id` | Integer FK | broader concept; tree stays the structure |
| `definition` | Text, nullable | one or two sentences: what belongs here, what does not |
| `status` | String | `approved` \| `provisional` \| `merged` (default `approved`) |
| `merged_into_id` | Integer FK, nullable | set when `status = merged`; history is kept |
| `origin` | String | `manual` \| `ai` \| `legacy` |
| `embedding` | BLOB, nullable | float32 of `name + definition + aliases` |
| `embedding_model`, `embedding_dim` | String/Integer, nullable | freshness guard (as Phase 2b) |
| `description` | String | existing; kept for short UI text |
| `color` | String | unchanged |
| `is_ai_suggested` | Boolean | existing; superseded by `origin` but kept for compatibility |
| `created_at`, `updated_at` | DateTime | |

`broader`/`narrower` are the existing parent/child edges. SKOS-style
`related` links are **not** in Phase 3 (see non-goals); the tree plus
aliases covers the actual drift observed so far.

### 3.2 `category_aliases`

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `category_id` | Integer FK | the canonical concept |
| `alias` | String | display form, e.g. "Billing" |
| `normalized` | String | lowercased/stemmed; **globally unique** |
| `source` | String | `manual` \| `ai` \| `merge` |
| `created_at` | DateTime | |

A globally unique `normalized` alias means one string resolves to at most
one concept. A conflict is never auto-resolved: it goes to the review
inbox. Aliases are how a merge is represented — the losing concept's name
becomes an alias of the winner, and its assignments are repointed.

### 3.3 `category_examples`

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `category_id` | Integer FK | |
| `content_hash` | String, nullable | document the example came from |
| `filename` | String | shown in prompts/UI |
| `summary_snippet` | Text, nullable | from Phase 2 facts, capped |
| `source` | String | `manual` \| `ai` |
| `created_at` | DateTime | |

Manual corrections write an example (and, when the concept is provisional,
count toward promotion). The nearest few examples per candidate concept are
injected into the assignment prompt.

## 4. Name resolution

When the LLM proposes a name (or the user types one), resolve it in this
order. Every step is a DB lookup or a cheap embedding comparison; no file
is re-read.

1. **Exact slug** under the intended parent (then globally).
2. **Alias** by `normalized` (global).
3. **Stemmed match** — light English stemming/plural folding against
   canonical names and aliases (e.g. "invoices" → "invoice").
4. **Embedding similarity** against `name + definition + aliases`:
   - ≥ `auto_alias_threshold` (proposed 0.92): reuse and add the proposed
     string as an alias.
   - `review_band` (proposed 0.82–0.92): queue a "possible duplicate" for
     the review inbox; assign to the closer concept meanwhile.
   - below the band: continue.
5. **New provisional concept** (`status = provisional`, `origin = ai`).

Embeddings reuse the Phase 2b embedding config. Without one, steps 1–3
still work; step 4 is skipped and new names become provisional directly.

**Implemented (3a).** `categories` gained `definition`, `status`, `origin`,
`merged_into_id`, and concept-embedding columns; `category_aliases`
(global unique `normalized`, D9) and `category_examples` exist. The
`ConceptResolver` implements the full order above, follows `merged_into`
links, creates provisional concepts, and lazily backfills concept
embeddings with the Phase 2b config. `categorize_files` resolves each path
segment through it. Alias add/delete endpoints and the new fields are in the
category API; the review inbox and consolidation are 3b/3c.

## 5. Assignment flow

### 5.1 Candidate retrieval

Instead of sending the whole tree in every prompt:

- Use the document's Phase 2b embedding to retrieve the nearest N
  (proposed 20) concepts by cosine over concept embeddings.
- Include each candidate's ID, canonical name, definition, and up to three
  examples.
- Also include the current parent/children neighbourhood so the model can
  choose a sibling when appropriate.
- If embeddings are unavailable, fall back to the current full-tree prompt.

### 5.2 Prompt contract

The LLM returns **concept IDs** for existing concepts and a `new_name` only
when nothing fits:

```json
{"files": [
  {"file_index": 0,
   "assignments": [
     {"concept_id": 12, "confidence": 0.93},
     {"new_name": "Parent > Child", "confidence": 0.71}
   ]}
]}
```

Constraints in the system prompt: maximum depth 4, maximum 12 children per
node, "strongly prefer an existing concept", "use `new_name` only if no
candidate is a reasonable fit", and "a new name must be a single leaf under
an existing parent when possible".

### 5.3 Manual corrections as learning

When the user adds or removes a category manually:

- Write a `category_examples` row (`source = manual`) for the chosen
  concept, with the document hash and summary snippet.
- For the removed concept, record a negative example only if the same
  concept was AI-assigned with high confidence (so the prompt can learn
  "not this").
- Future prompts for nearby documents include the nearest positive
  examples (by embedding or by same-category documents), not the whole
  history.
- Manual assignments remain `is_manual = true` and are never removed by
  re-categorisation (existing invariant).

**Implemented (3b).** Candidate retrieval ranks approved concepts by
cosine against the document's Phase 2b embedding (top 20), and the prompt
offers them by `concept_id` with up to two examples each; the full tree is
included only as a fallback when a file has no candidates. The parser
accepts `assignments` (`concept_id` or `new_name`) and the legacy
`categories` shape. Manual assignments write a positive `category_examples`
row (content hash + summary snippet); removing a confidently AI-assigned
category writes `manual_negative`. Provisional concepts auto-promote at
`concept_promotion_threshold` (default 3) and can be approved/rejected from
the review inbox (merge is 3c).

## 6. Provisional concepts and the review inbox

- AI-created concepts start `provisional`.
- **Auto-promotion:** after `promotion_threshold` distinct documents
  (proposed 3) or a manual approval, set `approved`.
- **Review inbox:** one low-friction screen listing provisional concepts
  with document count, sample filenames/summaries, and actions:
  - Approve (optionally rename/define),
  - Merge into an existing concept (creates an alias, repoints assignments),
  - Rename,
  - Delete (assignments move to a chosen concept or become uncategorised).
- Possible-duplicate pairs from §4 step 4 also appear here, with the two
  concepts side by side.
- The inbox is the *only* place merges are applied; the pipeline never
  merges on its own.

**Implemented (3b).** `GET /categories/review` lists provisional concepts
with document count, up to three document samples (filename + facts
summary), and near-duplicate approved concepts ranked by embedding/name.
`POST /categories/{id}/approve` (optional definition) and
`POST /categories/{id}/reject` (deletes the concept and its assignments)
back a Review tab in Settings. Merge and rename are intentionally left to
3c.

## 7. Consolidation pass

Two complementary mechanisms:

1. **Deterministic candidate detection** (always available): concept
   embedding cosine + normalized-name similarity produce a ranked list of
   possible duplicates. This is cheap and runs after taxonomy changes.
2. **LLM review** (on demand, and optionally after every N new documents):
   send a compact outline of the tree (names, definitions, counts) and ask
   for proposals: merge A→B, rename, or split a concept whose examples are
   heterogeneous. Proposals are **never applied automatically**; they land
   in the review inbox with a one-click apply.

Applying a merge is pure DB work:

1. Add the losing concept's name and aliases to the winner (`source=merge`).
2. Repoint `document_categories` and `file_categories` rows to the winner
   (dedupe pairs; manual beats AI; keep the highest confidence).
3. Set the loser `status = merged`, `merged_into_id = winner.id` (keep the
   row for history and old links).
4. No document is re-extracted, re-factored, or re-embedded.

## 8. Two-pass taxonomy proposal (optional slice)

The strongest fix for path dependence is to separate *proposing* the
taxonomy from *assigning* files:

1. Sample a diverse set of documents (by embedding clustering, not just the
   first N).
2. Ask the LLM for a compact taxonomy (depth ≤ 4, children ≤ 12) grounded
   in the sample.
3. Present it for approval/editing.
4. Assign files to the approved tree in normal batches.

This is optional for Phase 3 and should ship only after 3a–3c have soaked;
it changes the user's mental model from "the tree grows as I import" to
"review the tree, then import".

## 9. Drift metrics

Expose a small insights panel (and log the numbers in job summaries):

- singleton rate (concepts with one document / all concepts),
- concepts per 100 documents,
- provisional concepts awaiting review,
- possible-duplicate pairs above the review band,
- alias coverage (assignments resolved via alias vs new concept),
- confidence distribution of AI assignments.

These are read-only queries over existing rows; no new storage.

## 10. Migration and compatibility

- Existing categories become `status = approved`, `origin = legacy`,
  `definition = null`; slugs remain canonical; aliases and examples start
  empty.
- Existing assignments are untouched.
- Concept embeddings are backfilled lazily (on first assignment/consolidation
  run) and versioned like document embeddings.
- `is_ai_suggested` remains for API compatibility; `origin` is the new
  source of truth.
- `parent_id` semantics do not change, so the tree UI keeps working while
  the review inbox is added.

## 11. API and UI

- `GET /categories` gains `status`, `origin`, `definition`, `aliases`,
  `example_count` in flat/tree responses.
- `POST /categories/{id}/aliases`, `DELETE /categories/{id}/aliases/{alias_id}`.
- `POST /categories/{id}/merge` with a target id (applies the pure-DB merge).
- `GET /categories/review` (provisional + possible duplicates) and
  `POST /categories/{id}/approve|reject`.
- `GET /insights/taxonomy` for the drift metrics.
- UI: a **Review** tab in Settings (or a sidebar inbox) with approve/merge/
  rename actions; concept details show definition, aliases, examples, and
  document count. The existing category tree remains the browse surface.

## 12. Sequencing

| Slice | Contents | Payoff | Gate |
|---|---|---|---|
| 3a | Schema + migration (status/origin/definition, aliases, examples), resolution steps 1–3, provisional concepts, concept embedding backfill | Fewer synonyms; new names don't duplicate | Phase 2b embeddings for step 4 |
| 3b | Candidate retrieval + ID-based prompt, manual-correction examples, review inbox | Constrained assignment; learns from the user | 3a |
| 3c | Deterministic duplicate detection + LLM consolidation proposals + merge endpoint | Human-approved consolidation; no re-processing | 3b |
| 3d | Two-pass taxonomy proposal + drift insights | Removes path dependence; measurable quality | 3c, optional |

Each slice ships alone. 3a must not change assignment semantics; 3c must
never merge without approval.

## 13. Acceptance criteria

- Two names that normalize/stem to the same concept resolve to one concept;
  the second becomes an alias, not a new row.
- A manual correction writes an example and appears in later prompts for
  nearby documents.
- A provisional concept auto-promotes after the configured number of
  distinct documents, or on approval.
- A merge repoints assignments, adds the alias, keeps the losing row as
  `merged`, and performs zero extraction/LLM calls.
- Disabling embeddings degrades to slug/alias/stem resolution and the
  full-tree prompt; nothing breaks.
- Drift metrics are computable from the DB alone.
- Tests: resolution order, alias conflicts, provisional promotion, merge
  dedupe/confidence rules, manual-example injection, migration of legacy
  categories.

## 14. Open decisions

- **D8 — Categories vs a new concepts table.** Proposed: evolve
  `categories` (one identity, no assignment migration). Confirm.
- **D9 — Alias uniqueness.** Proposed: globally unique normalized alias,
  conflicts to review. Alternative: scoped per parent (allows "Reports"
  under Finance and Projects to mean different things). The tree already
  allows duplicate slugs under different parents; global alias uniqueness
  would forbid that for aliases. Needs a decision.
- **D10 — Auto-promotion threshold.** Proposed: 3 distinct documents or
  manual approval. Configurable.
- **D11 — Concept embeddings.** Proposed: reuse the Phase 2b embedding
  config; text = name + definition + aliases; auto-alias 0.92, review band
  0.82–0.92. Confirm thresholds.
- **D12 — Consolidation cadence.** Proposed: on demand, plus an optional
  prompt after every 100 new documents; never auto-apply. Confirm.

## 15. Non-goals

- A graph UI or arbitrary `related` links (tree + aliases first).
- Per-user/multi-user taxonomies (Phase 4 is views, not users).
- Re-categorising the corpus on every taxonomy change; merges are DB-only.
- Fully automatic merges without human approval.
- Replacing the LLM with a local classifier in this phase.
- Multi-language synonym expansion beyond what the LLM proposes and the
  user confirms.
