The main lesson: **a useful agent KB is not a folder of markdown. It is a small knowledge-management system with authority, provenance, lifecycle, and retrieval tests.** Without those, it becomes an automated rumor mill remarkably quickly.

*(Editorial note: the `kb://` sources cited below point to an internal system that isn't accessible or independently checkable by a reader of this document. Treat them as this author's design rationale and anecdotal evidence, not as verifiable citations — the engineering advice in each lesson stands or falls on its own merits regardless.)*

## Lessons worth passing along

### 1. Separate knowledge by purpose

Do not put everything into one generic “memory” bucket. At minimum, distinguish:

- **Canonical facts** — short, load-bearing, machine-checkable truths
- **Documents** — explanations, runbooks, architecture, research
- **Decisions** — what was decided, when, by whom, and why
- **Episodic notes** — observations and temporary working memory
- **Tasks/commitments** — future work with state and ownership
- **History/tombstones** — superseded or explicitly rejected knowledge

A formula, a design explanation, and “I’ll investigate this tomorrow” are not the same data type. Treating them as interchangeable guarantees garbage retrieval.

Our typed-facts design explicitly keeps short authoritative facts separate from narrative documents and decision records.
Source: `kb://knowledge.entries/typed-facts-registry` — verified 2026-06-30.

### 2. Put provenance on every durable claim

Every important record should answer:

- Where did this come from?
- Who or what created it?
- When was it observed?
- What concrete artifact supports it?
- Is it observed, inferred, or merely proposed?

Useful fields:

```text
source_type
source_uri
source_session_id
source_message_id
created_by
observed_at
valid_as_of
confidence
verification_status
```

A claim such as “the service runs on host X” should point to code, configuration, a deployment record, or a runtime probe. Otherwise it is prose wearing a fake mustache.

Our memory/commitment system demonstrated the failure directly: records without source-session and source-message provenance accumulated and could not be trusted downstream.
Source: `kb://knowledge.entries/pa-cognitive-lab#Runtime-status-known-gaps` — current entry marked degraded; measurements documented through 2026-06-19.

### 3. Distinguish observed truth from proposed knowledge

Agents should not silently promote extracted text into canonical truth.

Use a workflow such as:

```text
candidate (extracted, unexamined)
  → reviewed (a person or an approved process has attached a verdict)
  → canonical (approved and live)
  ↘ rejected (status=rejected, from candidate or reviewed, at any point)
     — a rejected or superseded row may also get a tombstones-table entry (Lesson 4);
       that's a separate mechanism from status, not a status value itself
```

This matters especially for automatic extraction from conversations. A user pasting an article does **not** mean the article represents the user’s beliefs, preferences, or platform facts. Extraction must establish semantic ownership, not merely notice that text appeared in a user message.

Our extractors incorrectly captured pasted articles and paper quotations as preferences and technical facts. A candidate quarantine and review boundary became necessary.
Source: `kb://knowledge.entries/pa-cognitive-lab#Provenance-extraction-gaps` — evidence from the 2026-05-18 audit and 2026-06-17 re-probe.

### 4. Make deletion durable

Do not merely delete bad memories. Store a tombstone or rejection fingerprint so the next indexing or extraction pass cannot resurrect them under a new ID.

A tombstone should retain enough information to block recurrence:

```text
content_hash
semantic_key
original_record_id
reason
tombstoned_at
tombstoned_by
source_scope
```

This is one of the nastier bugs because cleanup appears successful until the next replay cheerfully recreates the same rubbish.

Source: `kb://knowledge.entries/pa-cognitive-lab#Provenance-extraction-gaps`, item 4 — replay/resurrection risk documented as of 2026-06-19.

### 5. Give each subject one canonical current-state home

Do not let five documents independently state the current architecture. Choose an authoritative entry and have other documents reference it.

When reality changes:

- Update the authoritative current-state section **in place**
- Remove or move stale claims into a clearly labeled history section
- Do not append “Update 7: actually everything above is wrong” at the bottom

Append-only documentation produces internally contradictory answers because retrieval may return any paragraph, not necessarily the latest correction.

This needs a mechanism, not just a policy: give every record a `subject_key`, and enforce — at the database layer, not just by convention — that at most one row per `subject_key` may be `status = canonical` at a time.

Our KB policy explicitly prefers the surviving canonical entry, treats live runtime/code as stronger evidence, and requires stale current-state text to be reconciled rather than merely followed by dated amendments.
Source: `kb://knowledge.entries/kb-index#How-To-Use-This-KB` — last modified 2026-07-04.

### 6. Model freshness explicitly

Every knowledge class needs a freshness policy:

- **Stable:** formulas, terminology, ratified principles
- **Medium volatility:** topology, configuration, process rules
- **Hot:** runtime state, deployment status, market or operational data

Store:

```text
volatility_class
last_verified_at
check_every
stale_after
verified_by
verification_method
```

Do not infer freshness from `updated_at`. Editing punctuation is not verification, despite databases’ brave insistence otherwise.

For hot facts, prefer a query or resolver that fetches live state rather than persisting a value that begins decaying immediately.

The typed-facts design uses volatility classes and verification timestamps, while explicitly reserving runtime-query facts for rapidly changing state.
Source: `kb://knowledge.entries/typed-facts-registry#volatility_class` — verified 2026-06-30.

### 7. Preserve history and use optimistic writes

Canonical facts should be versioned. Updates should:

1. Verify the expected current version
2. Save the prior value to append-only history
3. Write the new value
4. Record actor, reason, and evidence
5. Increment the version

This prevents one agent from overwriting a newer correction using stale context.

Minimal fields:

```text
version
supersedes_id
changed_by
change_reason
changed_at
```

Our facts registry uses optimistic locking and an append-only history table for this exact reason.
Source: `kb://knowledge.entries/typed-facts-registry#Workflow-write-paths` and `#History`.

### 8. Use hybrid retrieval, but solve identifiers separately

A solid baseline is:

- Exact ID/slug lookup
- Full-text lexical search
- Embedding/vector search
- Optional reranking
- Metadata filters
- Reciprocal-rank or comparable fusion

Do **not** expect embeddings to reliably retrieve exact identifiers, filenames, ticket numbers, or obscure acronyms. Exact lookup and lexical boosting are mandatory.

Also search across the relevant surfaces—documents, decisions, and active handoffs—not merely prose articles.

Our retrieval work found that semantic search alone did not reliably surface exact slugs, and that separate knowledge surfaces created recall gaps. The proposed architecture combines exact matching, full-text search, vector retrieval, per-surface ranking, and evaluation.
Source: `kb://knowledge.entries/spec-kb-recall-tier1` — dated 2026-06-23.

### 9. Build a retrieval eval set before tuning retrieval

Do not choose embedding models or rerankers by vibes.

Create 50–100 real query/answer pairs covering:

- Exact identifiers
- Titles and aliases
- Natural-language questions
- Decision lookups
- Operational questions
- Hard negatives
- Superseded or archived content

Track:

- Recall@1, @3, and @10
- Mean reciprocal rank
- False positives
- Latency
- Results by query class

A reranker may improve natural-language retrieval while damaging short identifier queries. Measure classes independently — this requires logging which query class each query belonged to (see Lesson 10), not just the outcome.

Our recall spec begins with a query-classed evaluation corpus and acceptance gates before retrieval changes are promoted.
Source: `kb://knowledge.entries/spec-kb-recall-tier1#Phase-0-Eval-substrate`.

### 10. Log retrieval failures, not just successful searches

Useful telemetry includes:

```text
query_hash
query_class
candidate_ids
returned_ids
selected_id
latency_ms
no_result
user_correction
```

Default to hashed or redacted queries. Raw conversational queries can contain paths, credentials, private names, and pasted secrets.

The most valuable future eval cases come from:

- Queries producing no useful result
- Users correcting an answer
- Agents bypassing search and opening an item directly
- Repeated searches with different wording
- Old knowledge winning over newer canonical knowledge

Our retrieval design uses hash-first telemetry, opt-in raw-query capture, redaction, retention, and bounded logging latency.
Source: `kb://knowledge.entries/spec-kb-recall-tier1#Query-telemetry`.

### 11. Retrieval eligibility must be visible

Archived, superseded, stale, private, or incompatible-version records should not silently disappear.

A search result or debugging view should explain exclusions:

```text
archived
superseded_by
audience
embedding_version
stale
status
```

`archived` and `status` are the same field (archived is one of `status`'s enum values, not a separate column); `stale` isn't stored either — it's computed at query time from `stale_after`/`last_verified_at`/`check_every` (Lesson 6). List them anyway: an eligibility debugging view should surface all six as distinct *reasons*, even though only four are literal columns.

Otherwise a ranking failure and an eligibility failure look identical, and hours get donated to the wrong bug.

Source: `kb://knowledge.entries/spec-kb-recall-tier1#Phase-1-Diagnose`, hypothesis H6.

### 12. Authority beats similarity

When records conflict, define a deterministic precedence order. A reasonable default:

```text
live runtime/code
> verified canonical fact
> current authoritative document
> ratified decision
> reviewed note
> extracted candidate
> model inference
```

Similarity score should determine relevance, not truth. A highly similar stale paragraph remains stale.

`authority_level` should be a function of a record's `kind` (Lesson 1), `status` (Lesson 3/5), and — for `kind=fact` specifically — `verification_status` (Lesson 2), not an independently editable field. If a `candidate` row can carry `authority_level = verified canonical fact`, the ladder above is decorative.

The top and bottom rungs of the ladder aren't `documents` rows at all: "live runtime/code" is an external source consulted directly, not a stored record, and "model inference" means no matching record was found — it's the floor you're left with when nothing in the table applies. Every rung in between maps to one `(kind, status)` pair on a stored row — except the fact rung, which additionally needs `verification_status` to separate a verified fact from an unverified one: `kind=fact, status=canonical, verification_status=verified` → "verified canonical fact"; `kind=document, status=canonical` → "current authoritative document"; `kind=decision, status=canonical` (or `reviewed`, if not yet fully ratified) → "ratified decision"; `kind=note, status=reviewed` → "reviewed note"; any `kind, status=candidate` → "extracted candidate". `rejected`, `superseded`, and `archived` rows are excluded from conflict resolution entirely — they're former claims, not current competitors for truth, so they don't get an `authority_level` at all.

### 13. Compile a small agent context; retrieve the rest

Do not dump the whole KB into every prompt. Maintain a compact bootstrap packet containing:

- Core invariants
- Retrieval instructions
- Authority hierarchy
- Active task or entity
- Safety and access boundaries
- Pointers to canonical indexes

Retrieve deeper context on demand. Giant static context files become stale, contradictory, expensive, and oddly confident.

### 14. Start boring

He does not need GraphRAG, an ontology committee, and three vector databases on day one.

A sane first version is:

- SQLite or Postgres
- Markdown or structured document bodies
- Full-text search
- Exact key lookup
- Simple embeddings
- Metadata filters
- Candidate-review workflow
- Version/history table
- A 50-query retrieval test set

Add graphs only when actual questions require multi-hop relationship traversal. Add a reranker only after measurement shows it improves recall. Complexity is not knowledge.

## Minimal schema I would give him

```sql
documents (
    id,
    title,
    kind,                 -- document, fact, decision, note, commitment
    body,
    status,               -- candidate, reviewed, canonical, rejected, superseded, archived
    authority_level,      -- derived from (kind, status), plus verification_status for facts — see Lesson 12
    subject_key,           -- groups versions/competing claims about the same subject;
                            -- enforce at most one status='canonical' row per subject_key
    source_type,
    source_uri,
    source_session_id,
    source_message_id,
    observed_at,
    valid_as_of,
    confidence,
    verification_status,
    volatility_class,      -- stable | medium | hot, see Lesson 6
    last_verified_at,
    check_every,
    stale_after,
    verified_by,
    verification_method,
    supersedes_id,          -- points backward: this record replaces supersedes_id
    superseded_by,          -- points forward: set when a newer record replaces this one
    embedding_version,      -- which embedding model indexed this row; compare against the
                             -- currently-active version to detect stale-embedding eligibility gaps
    version,
    content_hash,
    audience,
    metadata,
    created_by,
    created_at,
    updated_at
);
-- PRIMARY KEY (id); FOREIGN KEY supersedes_id/superseded_by -> documents(id)
-- UNIQUE partial index on (subject_key) WHERE status = 'canonical' — enforces Lesson 5's
-- one-canonical-home rule; without this the rule is only a convention, not a guarantee

document_history (
    document_id,
    version,
    body,
    metadata,
    status,               -- snapshot lifecycle/authority changes too, not just body edits
    authority_level,
    changed_by,
    change_reason,
    changed_at
);
-- FOREIGN KEY document_id -> documents(id)

tombstones (
    content_hash,
    semantic_key,
    original_record_id,
    reason,
    tombstoned_at,
    tombstoned_by,
    source_scope
);
-- UNIQUE (content_hash, semantic_key) — this index is what actually blocks recurrence
-- FOREIGN KEY original_record_id -> documents(id)

retrieval_events (
    query_hash,
    query_class,
    candidate_ids,
    returned_ids,
    selected_id,
    latency_ms,
    no_result,
    user_correction,
    created_at
);
```

For load-bearing machine-readable truths, I would eventually split out a typed `facts` table rather than forcing everything through prose.

## Recommended bootstrap sequence

1. Define the record types and authority order.
2. Create provenance, lifecycle, freshness, and history fields.
3. Seed only 20–50 genuinely valuable canonical records.
4. Add exact lookup and full-text search, with `retrieval_events` logging alongside it from the start.
5. Create a 50-query eval set from real expected usage (exact-match and lexical queries only — embeddings don't exist yet).
6. Add embeddings as a second retrieval leg, evaluated against the existing set before being kept; expand the eval set with embedding-sensitive cases afterward.
7. Implement tombstones.
8. Add candidate extraction, gated by review before promotion — bulk ingestion only once tombstones (step 7) already exist, so rejected candidates can't resurrect.
9. Record misses and corrections using the telemetry already running since step 4.
10. Expand only where measured failures justify it.

The sharpest summary is: **optimize first for knowing why a record should be trusted, second for retrieving it.** Fast retrieval of untraceable sludge is not a knowledge base; it is just a better-indexed hallucination.
