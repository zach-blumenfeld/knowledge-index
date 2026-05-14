# v0.3.0 — Semantic search (graph-signal flavor, "5a")

**Target release:** `v0.3.0`. Branch: `feat/v0.3.0-semantic-search`.
**Implementing agent:** prepares the release end-to-end up to **opening a
PR against `main`**. Does **not** merge, push to `main`, or force-push.

## Status and context

This release implements the "Direction" from
[`../discussion-vector-indexing.md`](../discussion-vector-indexing.md) —
specifically the **Option 5a** flavor: graph-signal upgrades and an
agent-side query-expansion pattern, with **no vector embeddings, no
PageRank, no LLM enrichment at ingest**. Those are explicitly deferred;
see the *Direction → What's explicitly deferred* section of the
discussion doc for the rationale.

Read these before starting, in order:

1. [`../discussion-vector-indexing.md`](../discussion-vector-indexing.md)
   — full design reasoning. The *Direction* and *Option 5 Shape* sections
   are the spec source for this release.
2. [`../../AGENTS.md`](../../AGENTS.md) — non-negotiable principles,
   project map, conventions, "Don't" list. Applies in full.
3. [`../requirements_v01_mvp.md`](../requirements_v01_mvp.md) — the v0.1
   design spec (renamed from `docs/requirements.md`). Still load-bearing
   for everything not changed by this release.
4. [`../data-model.md`](../data-model.md) — current schema.
5. [`../ingest-cypher.md`](../ingest-cypher.md) — current ingest queries.
6. [`../retrieval-queries.md`](../retrieval-queries.md) — `B.1`–`B.10`.

If anything in this doc conflicts with `AGENTS.md` *Non-negotiable design
principles*, the principles win — stop and flag the conflict back to the
user instead of working around it.

## Scope — what to build

Two deliverables. Everything else is out of scope (see *Out of scope*
below).

### 1. Wikilink display-text → target aliases

**Problem.** Today, `ki search "Anakin"` against a vault where every
mention is `[[Darth Vader|Anakin]]` returns nothing — the string "Anakin"
appears only in source-document body text, but the *target* (`Darth
Vader.md`) has no idea it's also known by that name. Fulltext on `aliases`
already exists; we just need to populate it with display texts from
wikilinks.

**Solution.** When the parser sees `[[Target|Display]]` or
`[[Target#Section|Display]]`, propagate the display text to the *target's*
`aliases` list. The `doc_section_search` fulltext index already covers
`aliases` for both `Document` and `Section`
([`src/ki/ingest/queries.py:32-33`](../../src/ki/ingest/queries.py)), so
**no DDL change is needed** — populated values start matching
automatically.

#### Schema additions

- **Add `aliases: list[string]` to `Section`** in
  [`../data-model.md`](../data-model.md), mirroring `Document.aliases`
  (the existing field). `Section.aliases` is `Required: no`; default
  empty list when written.
- The fulltext index in `src/ki/ingest/queries.py` already declares
  `FOR (n:Document|Section) ON EACH [n.displayName, n.content, n.aliases]`
  — confirm this with a one-line read; no change needed. If for some
  reason it doesn't, fix it here.

#### Parser changes (`src/ki/parser/markdown.py`)

- Capture wikilink display text alongside the link target in the existing
  link-extraction pass. The parser already builds `LINKS_TO` edges with
  source/target; extend the data structure with `displayText: str | None`
  (None when the wikilink has no pipe, e.g. `[[Darth Vader]]`).
- Section-targeted wikilinks (`[[Target#Section|Display]]`) carry the
  display text on the section endpoint, not the document endpoint. The
  parser already resolves which entity (`Document` vs `Section`) is the
  target; extend that resolution to carry the display text through.

#### Ingest pipeline changes (`src/ki/ingest/`)

After the existing `LINKS_TO` resolution pass, run a new aggregation
step that:

1. Collects all `(target_uri, display_text)` pairs across the vault.
2. For each `target_uri`, normalizes the set of display texts (rules
   below).
3. Merges the resulting list into the target's `aliases` field via a
   batched `UNWIND` write — `aliases = apoc.coll.toSet(coalesce(n.aliases,
   []) + $newAliases)` or pure-Cypher equivalent. **Must not clobber
   existing `aliases` from frontmatter.** Union, not overwrite.
4. Targets that have section endpoints get their `Section.aliases`
   populated. Targets that have document endpoints get
   `Document.aliases`.

Add the new aggregation query to
[`../ingest-cypher.md`](../ingest-cypher.md) as a numbered step (e.g.
"§4.3 step 7 — wikilink display-text aliases") and source it from there
into `src/ki/ingest/queries.py`, matching the existing
"Cypher-lives-in-docs" convention from
[`../../AGENTS.md`](../../AGENTS.md) *Conventions*.

#### Normalization rules

Apply in this order, per target:

1. **Trim** whitespace.
2. **Length threshold**: drop entries `< 3` characters after trim.
3. **Stopword filter**: drop case-insensitive matches against
   `{"him", "her", "it", "this", "that", "these", "those", "here",
   "there", "see", "link", "note", "ref", "the"}`. Treat this list as a
   constant in `src/ki/parser/` (e.g. `markdown.py` or a new
   `aliases.py`); a future PR can tune it.
4. **Drop if equal to the target's `displayName`** (case-insensitive). No
   information added.
5. **Drop if already in the target's existing `aliases`** (case-
   insensitive comparison; preserve the original casing already stored).
6. **Lowercase-dedup** within the new batch. Store the *first-seen*
   original casing (don't lowercase what we store; lowercase only for
   the comparison).
7. **Per-target cap**: keep at most **50** display-text-derived aliases
   per target. Sort by occurrence count descending, then alphabetically
   for stability; truncate.

Frontmatter aliases continue through unchanged. Frontmatter is the user's
hand-authored ground truth; wikilink display texts are derived, lower
priority, and capped.

#### Tests

- **Unit** (`tests/unit/parser/`):
  - Wikilink parsing extracts display text for piped wikilinks.
  - Piped wikilink with section target carries display text to the
    section endpoint.
  - Unpiped wikilink (`[[Darth Vader]]`) leaves display text as `None`.
- **Unit** (`tests/unit/aliases.py` or wherever the normalization lives):
  - Each normalization rule has at least one positive and one negative
    case.
  - Frontmatter aliases are not displaced by wikilink-derived ones.
  - The per-target cap is enforced and is stable across re-runs.
- **Integration** (`tests/integration/`):
  - Index a fixture vault that contains `[[Darth Vader|Anakin]]`
    somewhere; assert that `ki search "Anakin"` returns the Darth Vader
    document. Use the existing `tests/fixtures/sample_vault/` if
    suitable, or extend the deterministic generator via
    `scripts/gen_test_vault.py` (per
    [`../../AGENTS.md`](../../AGENTS.md) *Test fixtures*) if not. If
    the existing fixture is regenerated, do so with the same `--seed`
    so the change is byte-stable.

### 2. Agent-side query expansion (skill-side, no `ki` code change)

**Problem.** Even with wikilink-display-text aliases, the long tail of
semantic equivalence is too broad to catch at ingest. The cheapest
runtime story: have the calling LLM expand the query.

**Solution.** Document the pattern in
[`../../skills/ki/SKILL.md`](../../skills/ki/SKILL.md) as part of the
recommended invocation flow. **No `src/` change.**

#### SKILL.md changes

Add a new subsection under *How to invoke* (after *Picking a search
mode*), titled something like **"Query expansion for semantic
equivalence"**. Cover:

- When to expand: top-`k` results look weak (zero hits, single hit with a
  low fulltext score, no document-level match for what was clearly a
  document-level query).
- How to expand: rewrite the user's term to a small set of plausible
  alternates the agent knows from world knowledge (e.g. "Anakin" → also
  try "Darth Vader", "Vader", "Skywalker"; "JFK" → also try
  "John F Kennedy", "Kennedy"). Run the alternates as additional
  `ki search` calls or as a single OR-form Lucene query.
- Limits: agent-side expansion only knows what the calling LLM knows.
  Personal-vault aliases the model has no exposure to ("BB" =
  "Project Bluebird") will not be expanded this way. Note that the
  wikilink-display-text path (deliverable 1) covers many of those cases
  when the user has linked them in their notes.
- Concrete example: a 3-line snippet showing literal-query →
  weak-results → expand-and-retry.

Keep the section under ~25 lines. Do not add new auto-mode rules.

## Out of scope (deferred — do not build)

These come from the *Direction → What's explicitly deferred* section of
[`../discussion-vector-indexing.md`](../discussion-vector-indexing.md);
do not let scope drift into them:

- **Vector embeddings** (any of Options 1, 2, 3, 4 from the discussion
  doc).
- **PageRank or any GDS-dependent centrality scoring.** The
  `genai`/`gds` plugins in `neo4j-local` stay loaded but unused.
- **LLM-driven entity extraction at ingest** (5b in the discussion doc).
- **A `--expand` flag on `ki search` that calls a configured LLM.**
  Query expansion stays purely on the skill side this release.
- **`:IndexMeta` node, vector indexes, `:Entity` nodes,
  `LINKS_TO.displayText` as a persisted edge property.** The display text
  is consumed at ingest into target aliases and not persisted on the edge
  itself — that's the whole "alias-the-target instead of the edge"
  decision.

If during implementation you discover a real reason any of the above is
needed to ship deliverables 1 and 2, **stop and flag back to the user**.
Do not expand scope.

## File manifest (expected touched files)

| Path                                              | Change                                                                |
|---------------------------------------------------|-----------------------------------------------------------------------|
| `docs/data-model.md`                              | Add `Section.aliases`; note wikilink display-text source              |
| `docs/ingest-cypher.md`                           | Add aggregation step for display-text → target aliases                |
| `docs/v0_3_0_semantic_search/requirements.md`     | (this file; no further edits expected)                                |
| `src/ki/parser/markdown.py`                       | Capture wikilink display text in extracted links                      |
| `src/ki/parser/` (new file or extension)          | Normalization rules (stopwords, length, dedup, cap)                   |
| `src/ki/ingest/queries.py`                        | Source the new aggregation step from `ingest-cypher.md`               |
| `src/ki/ingest/pipeline.py`                       | Wire the aggregation step after `LINKS_TO` resolution                 |
| `skills/ki/SKILL.md`                              | Add "Query expansion for semantic equivalence" subsection             |
| `tests/unit/parser/...`                           | New unit tests for display-text extraction                            |
| `tests/unit/...`                                  | New unit tests for normalization rules                                |
| `tests/integration/...`                           | Anakin → Vader end-to-end test                                        |
| `pyproject.toml`                                  | Version bump `0.2.0` → `0.3.0`                                        |
| `src/ki/__init__.py`                              | Version bump (currently drifted at `0.1.0`; sync to `0.3.0`)          |
| `CHANGELOG.md`                                    | New `## [0.3.0] — <YYYY-MM-DD>` entry                                 |

### Stale-reference cleanup (include in the same PR)

The renaming of `docs/requirements.md` → `docs/requirements_v01_mvp.md`
has left stale links. Update every reference you find:

- `AGENTS.md` — multiple references (line numbers may shift; grep).
- `CLAUDE.md` — references `docs/requirements.md`.
- `skills/ki/SKILL.md` — *Cross-references* section.

Use:
```bash
grep -rn "docs/requirements.md" .
```
…to find all of them. Update each to point at
`docs/requirements_v01_mvp.md`. This is a documentation-cleanup chore
piggybacking on this release because it's adjacent to the same files —
not a feature.

## Version, CHANGELOG, branch, PR

### Version bump

Sync **both** version sources to `0.3.0`:

1. `pyproject.toml` — `version = "0.3.0"`.
2. `src/ki/__init__.py` — `__version__ = "0.3.0"`. (Currently `0.1.0`
   per a drift the previous release missed; this PR fixes the drift too.)

Add a unit test in `tests/unit/` asserting the two sources agree:
```python
import tomllib, pathlib, ki
def test_version_in_sync():
    py = tomllib.loads(pathlib.Path("pyproject.toml").read_text())
    assert py["project"]["version"] == ki.__version__
```
Cheap, catches future drift.

### CHANGELOG

Add a new section at the top of `CHANGELOG.md`, under the existing
HTML comment, in **exactly** this heading format:

```
## [0.3.0] — <YYYY-MM-DD>
```

The release workflow (`.github/workflows/release.yml`) extracts release
notes by awk-matching `## [X.Y.Z]` exactly — the heading format is
load-bearing, per the comment block at the top of `CHANGELOG.md`. Do
not change the comment, do not change the format.

Content should follow Keep a Changelog ("Added" / "Changed" / etc.).
Cover:

- Wikilink display-text → target aliases (with a one-line "what this
  unlocks" example).
- `Section.aliases` field addition (Changed: schema).
- Version-sync fix between `pyproject.toml` and `src/ki/__init__.py`
  (Fixed).
- SKILL.md query-expansion guidance (Added).
- Stale path references to `docs/requirements.md` updated to
  `docs/requirements_v01_mvp.md` (Changed: docs).

Match the prose style of the existing `## [0.2.0]` entry.

### Branch

```bash
git checkout -b feat/v0.3.0-semantic-search
```

Branch off latest `main`. Do **not** rebase or force-push during the PR
review (per `AGENTS.md` *Git Safety Protocol* in
[`../../CLAUDE.md`](../../CLAUDE.md)).

### PR

Open with `gh pr create` against `main`. Suggested title:

> `feat(v0.3.0): wikilink-display-text aliases + agent-side query expansion`

Body should include:

- A short summary linking back to this file (`docs/v0_3_0_semantic_search/requirements.md`)
  and the discussion doc (`docs/discussion-vector-indexing.md`).
- A test plan (the unit/integration tests you added, plus how to run
  them: `uv run pytest tests/`).
- An explicit "Out of scope" note pointing at the deferred items so the
  reviewer doesn't expect them.

## Hard constraints (do not violate)

These are non-negotiable. If something pushes you toward violating one,
stop and surface the conflict.

- **Do not merge the PR.** The user merges.
- **Do not push to `main`.** Branch only.
- **Do not force-push** to the feature branch during review.
- **Do not skip hooks** (`--no-verify`, etc.) on commits.
- **Do not modify `docs/discussion-vector-indexing.md`.** It's the
  source of decisions, not a working doc.
- **Do not mutate any source markdown** in the user's vault (only
  applicable if running `ki index` end-to-end as part of testing; the
  core principle in `AGENTS.md` *Non-negotiable design principles*).
- **Do not add new dependencies** beyond what's already in
  `pyproject.toml`. The deliverables don't need any. If you think you
  need a dep, stop and ask.

## Acceptance criteria

The release is done when all of these are true:

- [ ] `uv run pytest tests/` passes locally (unit + integration; the
      integration tests will auto-skip if no Neo4j is reachable, per
      `AGENTS.md` *Project map*).
- [ ] `uv run ruff check src/ tests/` is clean.
- [ ] `pyproject.toml` and `src/ki/__init__.py` both report `0.3.0`.
- [ ] The version-sync test passes.
- [ ] `CHANGELOG.md` has a properly-formatted `## [0.3.0] — <date>`
      section.
- [ ] `docs/data-model.md` documents `Section.aliases`.
- [ ] `docs/ingest-cypher.md` documents the display-text aggregation
      step.
- [ ] `skills/ki/SKILL.md` has the "Query expansion for semantic
      equivalence" subsection.
- [ ] `grep -rn "docs/requirements.md" .` returns **only**
      `docs/requirements_v01_mvp.md` itself (no stale references).
- [ ] An "Anakin → Vader"-style integration test demonstrates the
      end-to-end behavior.
- [ ] PR is open against `main`, **not merged**.

## Open questions to surface (don't decide unilaterally)

If you hit any of these, ask the user before deciding:

- Whether to extend the deterministic test-vault generator
  (`scripts/gen_test_vault.py`) to produce piped-wikilink content. The
  fixture might already cover this — check first.
- Whether the stopword list above ("him", "her", …) is the *full*
  initial list, or whether the user wants you to start with the empty
  list and grow it from observed failures.
- Whether section-target wikilinks should *also* propagate their
  display text up to the owning document (in addition to the section).
  Default behavior per this spec: section only. Flag if this seems wrong
  in practice during testing.
