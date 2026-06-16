Awesome# Discussion: introspection and cross-vault dedup (v0.3.1 candidate)

> **Status: deliberation, not spec.** Two ideas to weigh for a potential
> v0.3.1 release. Each may land on its own; they're co-located here
> because they surfaced from the same observation.

## What triggered this

While listing vaults from the live graph during the v0.3.0 handoff, two
things became obvious:

1. There's no `ki` CLI surface for "show me my vaults" / "how many docs
   in vault X" / "find duplicates across vaults" / any other
   *introspection or analytics* query. The schema is documented and the
   graph is right there — `ki` just doesn't expose it. We dropped to
   `neo4j-cli` directly, which worked great but isn't documented as a
   pattern.

2. The graph had **two `:Vault` nodes that share a path prefix**:
   - `docs` at `/Users/zachblumenfeld/demo/knowledge-index/docs`
     (indexed standalone earlier)
   - `knowledge-index` at `/Users/zachblumenfeld/demo/knowledge-index`
     (indexed today, includes `docs/` as a subdirectory)

   Every markdown file under `docs/` now has **two `:Document` nodes** —
   one keyed under each vault id. `LINKS_TO` traversals don't cross
   between them. Storage is double-counted. Search returns duplicates.

The first observation is a skill-doc gap. The second is a schema
question that's been latent since v0.1 — `Document.uri` includes the
vault id, so identical content in two vaults is always two nodes. It
just didn't bite until two vaults overlapped.

## Item 1 — Introspection via direct Neo4j access

### Problem

`ki` ships five working verbs: `configure`, `index`, `search`, `rm`,
`init` (plus `skill`). The CLI surface is intentionally narrow —
[`requirements_v01_mvp.md` *CLI shape*](../requirements_v01_mvp.md)
explicitly calls out "three commands users actually need." That's the
right call for the working path. But it leaves a category of queries
unaddressed:

- **Inventory** — "what vaults do I have indexed?", "how many documents
  in each?", "what's the largest document?"
- **Health/dedup** — "are there documents sharing a `fileHash` across
  vaults?", "are any vaults nested inside another's path?"
- **Provenance forensics** — "which agent/model produced the latest
  ingest for vault X?", "show me `LOADED` edges by `loadId` for the
  last 24 hours."
- **Schema confirmation** — "what indexes / constraints exist right now
  on this Neo4j?"

None of these are *retrieval* queries (no `B.x` mapping). None justify a
dedicated CLI flag — they're tail-shaped, varied, low-frequency. But
agents currently have no idea this gap exists, so when a user asks one
of these questions, the agent either invents a `ki` flag (and gets a
"command not found") or gives up.

### Options

#### Option 1a — Document the pattern in `skills/ki/SKILL.md`

Add a short subsection telling agents: for **content search**, use
`ki search`; for **introspection / analytics / health checks**, drop to
`neo4j-cli query` against the same Neo4j the active `ki` profile points
at. Link to:

- [`docs/data-model.md`](../data-model.md) — the schema (label names,
  properties, relationships).
- The neo4j-cli skill — for the actual command shape (this skill is
  already a documented dependency per
  [`../../CLAUDE.md`](../../CLAUDE.md)).

Connection-credential discovery: agents read `~/.config/ki/config.yaml`,
extract the active profile's `uri` / `user` / `password`, and pass them
to `neo4j-cli query` via env vars (`NEO4J_URI`, `NEO4J_USERNAME`,
`NEO4J_PASSWORD`). Document this one-liner in SKILL.md.

**Pros**
- Zero `ki` code change. Pure skill-doc edit.
- Honest about the layering: `ki` is opinionated about *retrieval*;
  ad-hoc graph queries are a neo4j-cli concern.
- Same "two specialized tools, one mental model" pattern the project
  already uses for `neo4j-local` (config) and `neo4j-cli` (Aura
  lifecycle).

**Cons**
- Agents now need *two* skill files in scope when working with `ki`.
  Not a fundamental problem — `CLAUDE.md` already calls out neo4j-cli
  as the natural dependency — but it's a discovery cost.
- Reading `config.yaml` from a skill doc is a path the agent has to be
  given concretely. If the format changes, two skills drift.

#### Option 1b — Ship `ki cypher` as a thin passthrough

Add a `ki cypher "MATCH ..."` command that picks up the active profile,
opens a session, runs read-only Cypher, prints results. Essentially a
2-line wrapper around the neo4j driver, restricted to reads (the same
EXPLAIN-preflight pattern neo4j-cli uses).

**Pros**
- One credential surface. The user already configured `ki`; they don't
  need to also reach into `neo4j-cli`'s credential store or rummage in
  YAML for env vars.
- Discoverable via `ki --help`.

**Cons**
- Reinvents most of what `neo4j-cli query` already does (parameter
  binding, output formats, EXPLAIN preflight, embedding injection,
  schema introspection). Almost certainly we'd want a subset and then
  someone would want a feature `neo4j-cli` already has.
- Violates [`AGENTS.md`](../../AGENTS.md) *Non-negotiable design
  principles* point 2 (*backend is opaque*) — exposing raw Cypher
  surfaces Neo4j-as-backend in a first-class way.
- Adds a `ki` command users will probably never type. Agents would,
  but they're already happy to use `neo4j-cli` if pointed there.

#### Option 1c — Ship targeted introspection commands

`ki vaults list`, `ki vaults stats`, `ki doctor` (check for dup hashes,
overlapping paths, orphaned nodes), etc. Curated, opinionated, narrow.

**Pros**
- Cleanest UX for the few questions we *know* are common.
- Doesn't expose Neo4j-as-backend (each command returns a domain shape,
  not Cypher rows).

**Cons**
- Yet-another-command-list, in a project whose design ethos is "three
  commands users actually need."
- Tail-shaped — there will always be one more introspection question we
  didn't ship a flag for, putting us back at 1a anyway.
- Most cost for least flexibility.

### Tentative lean

**Option 1a.** It's the cheapest, it's the most honest about the
project's principles ("`ki` is for retrieval; the graph is open"), and
it composes well with the neo4j-cli skill already in the dependency
graph. Concretely:

- New SKILL.md subsection: **"Introspection and ad-hoc analytics
  queries"** under *Capabilities not yet wired* (or as a peer section).
- Three things in it: (a) the rule — content search via `ki`,
  graph-shape queries via `neo4j-cli`; (b) the credential-bridge
  one-liner; (c) two or three concrete worked examples (list vaults,
  count documents per vault, find duplicate `fileHash` across vaults).
- Cross-link the neo4j-cli skill and `docs/data-model.md` so the agent
  has the schema in front of it.

This pairs naturally with the v0.3.0 query-expansion subsection — both
are "make the agent smarter without making `ki` bigger."

## Item 2 — Hash-as-merge-key for cross-vault dedup

### Problem

`Document.uri` is currently `<vaultId>/<file path within vault>`
([`data-model.md`](../data-model.md) §Document). Two vaults that
contain the same file produce two distinct nodes — even if `fileHash`
is identical down to the byte. We just hit this with `docs/` indexed
both standalone and as part of the parent repo.

Same applies to sections (`Section.uri = <vaultId>/<file path>#<heading
slug>`), and the duplication cascades: `LINKS_TO` edges resolve within
a vault, so cross-vault content equivalence is invisible to `B.3`
(neighbourhood), `B.9` (backlinks), `B.10` (shortest path).

### Today's behavior, spelled out

- `Document.fileHash` is computed and stored per ingest
  ([`data-model.md`](../data-model.md) §Document, "drives incremental
  sync diffing"). It's the change-detection signal, not a merge key.
- Re-indexing an unchanged file: `fileHash` matches → skip via the
  Python-side hash check ([`requirements_v01_mvp.md` *Auto-sense on
  `ki index`*](../requirements_v01_mvp.md)). Never reaches Cypher.
- Re-indexing a *different* file with identical content: two nodes,
  same `fileHash`. The graph has no idea they're equivalent.

### Proposal under consideration

Make the *content hash* the merge key. Options range from radical to
conservative.

#### Option 2a — Hash IS the URI

`Document.uri = sha256(content)` (or `sha256(displayName + content)` if
filename matters). Vault membership moves entirely to the
`HAS_DOCUMENT` edge. `LOADED` provenance from multiple vaults
accumulates on the same document.

**Pros**
- Automatic dedup across vaults. The motivating case "just works."
- Content nodes are content-canonical: the schema reflects "this is the
  same writing" without per-vault qualification.
- File renames become free — same content, same node, the path moves
  on the edge.

**Cons**
- **Content edits create new nodes.** Today, editing `ideas.md` updates
  `Document.fileHash` on the same node. With hash-as-uri, the edit
  produces a new `uri`, leaving an orphan. Either we cascade-delete the
  old node (losing `LOADED` history) or accumulate orphans (graph rot).
  Neither is good.
- **`LINKS_TO` resolution gets harder.** Today, `[[Project Bluebird]]`
  resolves to the document whose path-derived URI matches. With
  hash-as-uri, we need a separate name → uri lookup (and that lookup
  itself becomes ambiguous when two unrelated documents share a name).
- **`LOADED` provenance semantics shift.** Today, `LOADED` is "this user
  loaded this document from this vault at this time." With cross-vault
  dedup, the *same* `Document` node is loaded by multiple vaults, and
  `LOADED.loadId` no longer uniquely identifies an ingest event without
  reading the vault context off the `HAS_DOCUMENT` edge. Doable, but a
  schema rethink.
- **Section identity is even messier.** `Section.content` includes
  `uri:` references to direct child sections (per
  [`data-model.md`](../data-model.md) *Content Construction Rules*).
  If child URIs are hash-derived, parent content changes on any leaf
  edit, propagating up the tree. Hash cascades render the whole
  section subtree's nodes ephemeral.
- **`ki rm --vault` becomes reference-counted.** Removing a vault
  shouldn't drop a `Document` shared with another vault. Either we
  cascade only orphaned nodes or we expose explicit ref-counting. Both
  are net-new complexity.

#### Option 2b — Hash drives a `:Content` node; URI stays path-based

Keep `Document.uri` path-based for *identity* and *link-resolution*,
but introduce a `:Content` node keyed by `fileHash`. `Document
-[:HAS_CONTENT]-> Content`. Search-time aggregation collapses by
`Content`.

**Pros**
- Existing URI semantics, `LINKS_TO` resolution, `LOADED` provenance
  all unchanged.
- Cross-vault dedup is *expressible* (group hits by `Content`) without
  forcing it everywhere.
- Section.content can stay path-based; only the `:Content` node tracks
  byte-identity.

**Cons**
- An extra node type and an extra edge per document, paying for a
  problem most users won't have until they nest vaults.
- Search-side aggregation logic on every retrieval — small, but real.
- "Cross-vault dedup" becomes a *behavior of search* rather than a
  *property of the schema*. Not necessarily bad, but a different shape.

#### Option 2c — Detect and warn at ingest, no schema change

When `ki index` would write a `Document` whose `fileHash` already
exists under a different vault, print a warning ("`X.md` content
matches an existing document in vault Y") and proceed. The user
decides whether to deduplicate by removing one of the vaults.

**Pros**
- Zero schema impact. Zero query-side impact. Pure ingest-side
  observability.
- Honest about who owns the decision: the user picks which vault to
  keep, `ki` just surfaces the duplication.
- Composable with `ki rm --vault` already shipping for v1.

**Cons**
- Doesn't actually fix the cross-vault retrieval gaps (duplicated
  `B.1` / `B.2` hits, broken cross-vault `LINKS_TO` traversal).
- Vault-overlap is detected only at ingest. Re-ingesting one vault but
  not the other can leave the warning stale.

#### Option 2d — Path-overlap detection at vault creation

Before initializing a new vault, check whether the proposed path is
nested inside (or contains) any existing vault's path. Refuse with a
clear error or prompt. Doesn't address byte-identical content in
unrelated locations, but it *would* have caught the specific
`docs/` / `knowledge-index/` case that triggered this discussion.

**Pros**
- Prevents the most common cause of cross-vault duplication (nested
  paths) entirely.
- Zero schema impact, zero query-side impact.

**Cons**
- Doesn't catch byte-identical content shared across unrelated paths
  (Dropbox-sync of the same notes folder under different mount points,
  e.g.). Solves the easy case, leaves the harder case open.

#### Option 2e — Post-hoc `apoc.refactor.mergeNodes` consolidation

Keep the schema as-is. Add a new command (e.g. `ki resolve` or
`ki dedupe`) that finds `:Document` nodes sharing a `fileHash` across
vaults and merges them via
[`apoc.refactor.mergeNodes`](https://neo4j.com/docs/apoc/current/overview/apoc.refactor/apoc.refactor.mergeNodes/),
preserving min(`firstLoadedAt`) and max(`lastLoadedAt`), union-ing
`aliases`, and letting APOC redirect all `HAS_DOCUMENT` / `LOADED` /
`LINKS_TO` / `HAS_SECTION` edges onto the surviving node.

Sketch:
```cypher
MATCH (d:Document)
WITH d.fileHash AS hash, collect(d) AS docs
WHERE size(docs) > 1
CALL apoc.refactor.mergeNodes(docs, {
  properties: {
    firstLoadedAt: 'discard',   // we'll override below; min in post-step
    lastLoadedAt:  'discard',   // ditto
    aliases:       'combine',
    `.*`:          'discard'    // keep surviving node's defaults
  },
  mergeRels: true
})
YIELD node
// Post-step: re-compute firstLoadedAt / lastLoadedAt from incoming LOADED edges
...
RETURN node
```

**Pros**
- **No schema change.** `Document.uri` semantics, `LOADED` provenance,
  `LINKS_TO` resolution all stay as documented in
  [`data-model.md`](../data-model.md). No breaking release, no
  migration.
- **Idempotent.** Re-running just merges any new duplicates. Composes
  with item 1's "let the agent detect duplicates via Cypher" — the
  agent can both find and fix.
- **APOC does the edge plumbing.** `mergeRels: true` collapses
  duplicate `HAS_DOCUMENT` / `LOADED` / `LINKS_TO` edges automatically.
  We don't write that logic.
- **Opt-in, on-demand.** Doesn't slow `ki index` if it's a separate
  command. Could also auto-run at the end of `ki index` if we decide
  the perf hit is acceptable (a single Cypher pass; should be cheap).
- **Reversible by re-ingest.** If the user wanted the vaults
  separate, deleting one and re-running `ki index` recreates the
  distinct nodes. Not a clean undo, but a real escape hatch.

**Cons (the ones the "no schema change" framing glosses over)**

- **Sections are *not* dedup'd by this.** `Section` has no `fileHash`
  today; only `Document` does. So merging two documents with identical
  content leaves their `Section` children **separately**:
  - The merged `Document` now has `HAS_SECTION` edges to **both** vaults'
    section trees — `S1ᴬ, S2ᴬ` *and* `S1ᴮ, S2ᴮ`, all with vault-id-
    prefixed URIs that diverge.
  - `B.2` (section content fulltext) still returns duplicates.
  - `B.4` (document text via `NEXT_SECTION`) — the merged document now
    has *two* `NEXT_SECTION` chains stitched off two different "first
    sections." The walk in `retrieval-queries.md` B.4 picks whichever
    section has no incoming `NEXT_SECTION`; with two such heads, the
    result is ambiguous and order-dependent. **This is a real
    correctness issue, not just an aesthetic one.**

  Two ways out: (i) add `Section.contentHash`, run the same APOC merge at
  section granularity — small, additive schema change, not breaking;
  (ii) on document-merge, also delete one vault's `Section` subtree
  (lossy and brittle — `LINKS_TO` edges pointing at the deleted sections
  need re-pointing, which APOC won't infer for us).
  Option (i) is much cleaner and probably how this option should land
  if we pick it.

- **`ki rm --vault` semantics shift.** Today:
  `MATCH (v:Vault {uri:$uri}) DETACH DELETE v` cascades through
  `HAS_DOCUMENT` and removes the documents (per
  [`requirements_v01_mvp.md` *Removal*](../requirements_v01_mvp.md)).
  Post-merge, documents are shared. A naive cascade would delete a
  document that the *other* vault still references. We'd need
  reference-counted deletion: only remove a `Document` when no other
  `:Vault` still has a `HAS_DOCUMENT` edge to it. Not a schema change,
  but a *behavioral* change to `ki rm` and a real semantic shift —
  "remove this vault" no longer means "remove all its nodes."

- **`Document.uri` becomes a lie for one of the vaults.** The merged
  node carries a single `uri` like `<vault-A-id>/foo.md`, but the same
  document is now accessible from vault B's `HAS_DOCUMENT` edge.
  Callers can't infer vault membership from the uri alone — they have
  to traverse `HAS_DOCUMENT`. That's already mostly true today, but
  the uri-as-shorthand pattern breaks subtly. Minor, but worth knowing.

- **Property merge strategy needs a spec.** `lastLoadedAt` → max,
  `firstLoadedAt` → min are clear. `aliases` → union (important: the
  v0.3.0 wikilink-display-text-aliases work survives merge correctly).
  But `frontmatter`, `displayName`, `name`, `sourceType` — usually
  identical across vaults, but what if they differ? APOC's `combine`
  produces arrays; `discard` keeps the surviving node's value. Need a
  one-time decision, not a free lunch.

- **APOC availability across deployments.** `apoc.refactor.*` is in
  APOC Core. `neo4j-local` ships APOC by default
  ([`requirements_v01_mvp.md`](../requirements_v01_mvp.md)). Aura ships
  APOC Core on every tier including Free. Self-hosted Community /
  Enterprise — APOC Core is a plugin install the user has to do, but
  it's a well-known one. So deployment-parity is good, but the
  feature does pull in an APOC dependency we didn't previously rely on
  for *correctness* (we only relied on it for `neo4j-local` plugin
  inventory).

- **Lossy provenance for the *node identity*.** APOC keeps one
  surviving node; the other is gone. If anyone outside `ki` was
  caching the lost `uri`, those references go stale. Not a real
  problem inside `ki` (graph relationships transfer to the surviving
  node), but worth flagging.

### Tentative lean

Two viable shapes — both reject 2a (radical schema rework) and 2b
(speculative `:Content` node):

**Shape A: 2d + 2c (prevent + warn).** Don't actually fix existing
duplication; make it hard to create and visible when it slips through.
Smallest scope, fastest to ship, zero behavioral changes to existing
commands.

**Shape B: 2d + 2c + 2e (prevent + warn + fix).** Same prevention layer,
plus a `ki resolve` (or `ki dedupe`) command that actually consolidates
duplicates via `apoc.refactor.mergeNodes`. **No schema change, but
*does* require: (i) adding `Section.contentHash` and merging sections
too, otherwise B.4 breaks; (ii) reference-counted `ki rm --vault`
behavior; (iii) a property-merge strategy spec.** All additive, all
non-breaking, but real work — not "side-steps issues."

Pick between them based on whether **byte-identical content across
non-nested paths** is a real case we expect. If it is (Dropbox-synced
vault on two machines, same notes folder under two mount points, a
vault copy used as a scratch space), Shape B is justified. If the
motivating case is just "I accidentally nested vaults," Shape A
suffices and 2d alone would have prevented it.

Said differently:

- **Shape A** = make the bug hard to hit, make it visible when it does.
  Pulls us toward 2e only if real demand materializes.
- **Shape B** = also fix the bug when it does happen. Costs us a small
  schema addition (`Section.contentHash`), a `ki rm` behavior shift,
  and one well-defined APOC pass. Buys us actual remediation, not just
  prevention + observability.

Concrete recommendation: **start with Shape A, with 2e listed as
"queued — promote when demand is concrete."** The `Section.contentHash`
addition + ref-counted `ki rm` are not free; let the value get proven
first. *But*: if we're going to land item 1 (the introspection skill
add) in the same release, the agent can self-serve duplicate detection
via Cypher post-Shape-A — which means we'll see *whether* duplication
beyond 2d's coverage is actually happening. That observation is the
gate for promoting 2e.

### Open questions for item 2

- What should `ki index ./docs` do when `/Users/.../knowledge-index` is
  already an indexed vault? Refuse? Prompt? Auto-fold into the parent
  vault? (Same question in reverse if the order is flipped.) This is
  the 2d behavior spec.
- Should `Section.uri` ever participate in dedup, or only `Document`?
  If we pick Shape B, the answer is **yes** — `Section.contentHash`
  becomes the merge key for sections, and the `NEXT_SECTION` chains
  collapse correctly. If Shape A, this is moot.
- For 2e's property-merge strategy: how do we resolve conflicting
  `frontmatter` / `displayName` / `name` across the two merge inputs?
  Likely: keep the surviving node's values (discard), since
  byte-identical content should produce byte-identical extracted
  metadata anyway — if it doesn't, something upstream is wrong and
  warrants a warning, not a silent reconciliation.
- For 2e: where does the merge run — end of `ki index`, or only on
  demand via `ki resolve`? End-of-ingest gives consistency at the cost
  of perf; on-demand keeps ingest fast and gives the user agency.
- If 2b lands later (low probability if 2e is doing the job), does
  `:Content` carry `displayName`? `aliases`? Or are those stuck on
  `Document` because they're authorship-bound, not byte-bound?

## How these two items interact

They don't conflict; they barely interact. But there's one nice
synergy:

If item 1 ships first — "for introspection, drop to `neo4j-cli`" — the
agent acquires the tooling to *detect* duplication on its own
(`MATCH (d:Document) WITH d.fileHash AS h, collect(d) AS docs WHERE
size(docs) > 1 RETURN h, [doc IN docs | doc.uri]`). That, paired with
2c's ingest-time warning, gives the user both a proactive surface (the
warning at ingest) and a reactive surface (ask the agent "any
duplicates?" and have it run the Cypher). Either alone is okay; both
together is the cheap, complete answer to the motivating problem.

## Ordering

- Item 1 (skill-doc only) is a ~1-hour edit and a clear win. Could
  fold into v0.3.0 if scope permits, or land as v0.3.1 on its own.
- Item 2 is a real design question. The conservative subset (2d + 2c)
  is small but worth its own thinking time before locking in. Likely
  v0.3.1 if we want both; otherwise v0.3.2.

Do **not** bundle item 1 into the v0.3.0 PR retroactively — that PR is
already specified at
[`../v0_3_0_semantic_search/requirements.md`](../v0_3_0_semantic_search/requirements.md)
and adding scope mid-flight is the surest way to slip the release.
Schedule item 1 separately.
