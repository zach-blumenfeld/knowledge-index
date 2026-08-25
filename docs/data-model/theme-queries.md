# Theme producer — GDS queries + pipeline script

Producer side of [`docs/commands/theme-format.md`](../commands/theme-format.md). Shipped as `ki theme`; the executable copies of these queries live in `src/ki/themes/queries.py` and the pipeline in `src/ki/themes/producer.py` — change the doc and the code together (AGENTS.md *Conventions*).

Requires the **GDS plugin**, which ships in ki's default local container (`NEO4J_PLUGINS` in `src/ki/neo4j_podman.py`). It is a **hard requirement with no fallback** — a GDS-less profile gets a capability error naming the plugin, never a second-class grouping ([#72](https://github.com/zach-blumenfeld/knowledge-index/issues/72)).

**Driven by plain Cypher, not the `graphdatascience` Python client.** The procedures are called directly over the stock driver (`CALL gds.leiden.mutate(...) YIELD ...`). The client would drag pandas and pyarrow into a CLI whose entire dependency set is seven small packages, and buys nothing here — the pipeline is nine statements. Signatures below are verified against **GDS 2026.04.0**. The Python-client form (`gds.v2.leiden.mutate(...)`) appears nowhere in the shipped code; earlier drafts of this doc used it because the prototype was a standalone script.

Pipeline: **project doc-level link graph (docs + glue nodes) → Leiden (mutate `themeId`) → per-community conductance → write `themeId` → renderer queries → drop projection.**

Design decisions baked in:

- **Document-level themes.** Section-level `LINKS_TO` edges are collapsed to their owning documents (a Section's owning doc URI is everything before `#` in its own URI — no `HAS` walk needed).
- **Scope:** one vault (`uri STARTS WITH $vaultPrefix`, trailing `/` kept). Theme **membership** is `sourceType = 'LOCAL_FILE'` docs only, but `LOCAL_STUB` and `WIKILINK_UNRESOLVED` nodes are projected as **glue nodes**: they participate in clustering so that two docs citing the same `[[concept]]` or attachment can land in the same theme even when they never link each other (co-citation — most of the signal in journal-style vaults). Glue nodes are filtered out of membership at read time. Bare `URL_LINK`s stay excluded — a frequently-pasted URL is a hub that welds unrelated docs together, and the vault-prefix filter drops them anyway.
- **Undirected, weighted.** GDS Leiden is defined on undirected graphs; weight = number of links between the doc pair (parallel `LINKS_TO` edges count).
- **Leiden** for community detection (refinement of Louvain — guarantees well-connected communities). `random_seed` + `concurrency=1` for deterministic theme assignment within an index generation (theme-format.md stability contract).
- **Theme floor:** `min_theme_doc_count` (default 3) — themes with fewer `LOCAL_FILE` member docs fold into ungrouped after write-back (§4c). Not delegated to Leiden's `minCommunitySize`, which counts projected nodes including glue.
- **Top-k cut:** `--top-k K` renders only the K biggest themes for first-use lay-of-the-land at minimal context cost. **Display-only, after Leiden** — unlike the floor, hidden themes are real: they keep their `themeId`s and stay counted as grouped; the header accounts for them (`showing top K — H smaller themes cover D more docs`) and crossovers into hidden themes are suppressed.
- **Exclusions:** `--exclude <doc>` (repeatable; basename or vault-relative path, case-insensitive) removes hub docs (`CLAUDE.md`, `index.md`, MOC pages) from the **entire universe** — projection, membership, binds targets, crossovers, and header denominators — not just from the display. Hub edges distort the communities themselves, so filtering must happen before Leiden, not after. Excluded docs surface honestly in the header (`· N excluded`), never silently. Predicate: `toLower(d.name) IN $exclude OR substring(d.uri, size($vaultPrefix)) IN $exclude`, applied to both ends of every match (see `scripts/theme_producer.py`).
- **Cohesion word** (`tightly / moderately / loosely interlinked`) derives from per-community **conductance** (`gds.conductance` metric over the mutated `themeId` — the fraction of a theme's link mass that leaves the theme; low = tight). One in-memory metric call, no extra Cypher pass, not size-biased the way edge density is, and it sees glue edges so co-citation-held themes are measured fairly. Thresholds live here, never in the output. (An earlier draft blended per-community modularity with doc–doc edge density; dropped — both inputs were biased and the density query cost a second full pass over the vault's links.)

## 1. Doc-level pair query (renderer queries, §6)

Collapses `(Document|Section)-[:LINKS_TO]->(Document|Section)` to `LOCAL_FILE` doc pairs with a link-count weight. The **projection (§2) uses a relaxed variant** of this match that additionally keeps glue-node targets; this strict doc-doc form is what the renderer queries reuse. Pairs stay **directed** here — a mutually-linking pair yields two rows (A→B w=3, B→A w=2). That's fine: under the undirected projection Leiden sums parallel relationship weights, so two undirected rels of 3+2 are mathematically identical to one edge of 5 (the quality function only sees weighted degrees and intra-community weight sums). No canonical-ordering step needed.

```cypher
// $vaultPrefix = '<vaultUri>/'
MATCH (src)-[l:LINKS_TO]->(tgt)
WHERE src.uri STARTS WITH $vaultPrefix
  AND tgt.uri STARTS WITH $vaultPrefix
MATCH (s:Document {uri: split(src.uri, '#')[0]})
MATCH (t:Document {uri: split(tgt.uri, '#')[0]})
WHERE s.sourceType = 'LOCAL_FILE'
  AND t.sourceType = 'LOCAL_FILE'
  AND s <> t
WITH s, t, count(*) AS weight
RETURN s, t, weight
```

The `tgt.uri STARTS WITH $vaultPrefix` filter drops `URL_LINK` externals (their URIs are `https://…` / `file:///…`); the `sourceType` filter drops `LOCAL_STUB` and `WIKILINK_UNRESOLVED`. Both `Document.uri` lookups hit the uniqueness constraint's index.

### Step-by-step (full projection form, §2)

```cypher
MATCH (src)-[l:LINKS_TO]->(tgt)
```
Every link edge in the graph. `src`/`tgt` are label-free on purpose — `LINKS_TO` endpoints can be `Document` *or* `Section`, and we want all four combinations.

```cypher
WHERE src.uri STARTS WITH $vaultPrefix
  AND tgt.uri STARTS WITH $vaultPrefix
```
Scope both ends to one vault via the hierarchical-URI prefix (`<vaultUri>/`). Side effect: this also drops `URL_LINK` externals for free, since their URIs are `https://…` / `file:///…` and can't match the prefix.

```cypher
MATCH (s:Document {uri: split(src.uri, '#')[0]})
MATCH (t:Document {uri: split(tgt.uri, '#')[0]})
```
Collapse to the **owning document**. A Section URI is `<doc-uri>#<heading-path>`, so `split(uri, '#')[0]` is the doc URI; for a Document it's a no-op (no `#`). Each lookup is an equality match on `Document.uri`, so it hits the uniqueness constraint's index — the cheap alternative to walking `HAS*` upward.

```cypher
WHERE s.sourceType = 'LOCAL_FILE'
  AND t.sourceType IN ['LOCAL_FILE', 'LOCAL_STUB', 'WIKILINK_UNRESOLVED']
  AND s <> t
```
The source side must be a parsed internal doc — themes are *made of* `LOCAL_FILE` docs, and only they originate links. The target side additionally admits `LOCAL_STUB` and `WIKILINK_UNRESOLVED` as **glue nodes**: they cluster along with the docs, so two docs that both cite `[[GraphRAG]]` (a node with no file behind it) sit two hops apart and can land in the same community despite never linking each other. `URL_LINK` is already gone via the prefix filter — the explicit `IN` list documents that its exclusion is deliberate (pasted-URL hubs weld unrelated docs together). Self-links discarded — a doc linking to its own sections says nothing about themes.

```cypher
WITH s, t, count(*) AS weight
```
Cypher's implicit grouping: grouping key = `(s, t)`; aggregate = `count(*)`. One row per **directed** doc pair, `weight` = number of link instances in that direction, whatever sections they came from. A mutually-linking pair produces two rows — deliberately left as-is: under the undirected projection below, Leiden sums parallel relationship weights, so two undirected rels of w=3 and w=2 are mathematically identical to one canonical edge of w=5 (the quality function only sees weighted degrees and intra-community sums). An earlier draft canonicalized the pair ordering here; it bought nothing but a smaller projection.

```cypher
RETURN gds.graph.project(
  $graph_name, s, t,
  { relationshipProperties: { weight: weight } },
  { undirectedRelationshipTypes: ['*'] }
)
```
The Cypher-projection aggregation function — called once per row, building the in-memory graph as rows stream through. Args: graph name; source/target nodes (only nodes that appear in some edge enter the projection — docs with no links at all are absent, which is exactly the "ungrouped" set; docs whose only links go to glue nodes *are* in); a data map putting `weight` on the projected relationship (Leiden reads it via `relationshipWeightProperty`); a config map declaring every relationship undirected — required because GDS Leiden is only defined on undirected graphs.

## 2. Projection (plugin Cypher projection, undirected + weighted)

```cypher
// $graphName = ki-theme-<sanitized vault uri>, $vaultPrefix = '<vaultUri>/'
MATCH (src)-[l:LINKS_TO]->(tgt)
WHERE src.uri STARTS WITH $vaultPrefix AND tgt.uri STARTS WITH $vaultPrefix
MATCH (s:Document {uri: split(src.uri, '#')[0]})
MATCH (t:Document {uri: split(tgt.uri, '#')[0]})
WHERE s.sourceType = 'LOCAL_FILE'
  AND t.sourceType IN ['LOCAL_FILE', 'LOCAL_STUB', 'WIKILINK_UNRESOLVED']
  AND s <> t
WITH s, t, count(*) AS weight
RETURN gds.graph.project(
  $graphName, s, t,
  { relationshipProperties: { weight: weight } },
  { undirectedRelationshipTypes: ['*'] }
) AS g
```

`gds.graph.project` here is the **aggregation function**, not the procedure of the same name — it is called once per row as pairs stream through, building the in-memory graph incrementally.

This is the §1 match with the target filter relaxed to admit **glue nodes** (`LOCAL_STUB`, `WIKILINK_UNRESOLVED`) — see the step-by-step above. Docs with no links at all never enter the projection — they come out as **ungrouped** (header `<U>`), computed in step 6 as `total_docs − grouped`. No WCC pre-pass needed: Leiden handles a disconnected projection (each component clusters independently).

## 3. Leiden → `themeId` (mutate, not write)

```cypher
CALL gds.leiden.mutate($graphName, {
  mutateProperty: 'themeId',
  relationshipWeightProperty: 'weight',
  gamma: $gamma,               // resolution; >1 -> more, smaller themes
  randomSeed: 42,
  concurrency: 1               // required for the seed to make runs deterministic
}) YIELD communityCount, modularity
RETURN communityCount, modularity
```

`modularity` is kept only as a whole-run sanity number (> 0.3 means real structure); it plays no part in per-theme cohesion.


**`mutate`, deliberately not `write`:** §4's conductance metric reads `themeId` from the *in-memory* graph, and the projection never sees DB writes — so a one-step `leiden.write` would persist the property but leave the projection without it, breaking §4. Mutate first; persistence to Neo4j happens in §4 via ``gds.graph.nodeProperties.write` after the metric runs.

`themeId` lands on every projected node — `LOCAL_FILE` docs *and* glue nodes. That's fine: membership queries filter to `LOCAL_FILE` at read time, and a glue node's `themeId` is potentially useful later (e.g. tagging `top wikilink targets` with their home theme). It is index-generation state, owned by ki (not user-authored — consistent with design principle #1); a re-index that reruns the producer overwrites it.

## 4. Per-community conductance (cohesion input)

```cypher
CALL gds.conductance.stream($graphName, {
  communityProperty: 'themeId',
  relationshipWeightProperty: 'weight'
}) YIELD community, conductance
RETURN community, conductance
```

`conductance` is the fraction of the community's link mass that crosses its boundary: 0 = fully self-contained, →1 = mostly leaks out.


> **Note — mutate vs write ordering.** The in-memory graph does not see DB writes, so `community_property` must exist *in the projection*. Run Leiden in **`mutate`** mode first (`mutate_property="themeId"`), stream conductance off the mutated property, then persist (below). The script does exactly this.

Leiden's run-level `modularity` (§3 result) is kept only as a sanity number for the whole clustering (> 0.3 ≈ real structure); it plays no role in per-theme cohesion.

**Write-back (three sub-steps — order matters).** Until these run, `themeId` exists only in the projection and the §6 renderer queries (plain Cypher filtering on `d.themeId`) see nothing.

**4a. Clear stale `themeId`s from prior runs.** `node_properties.write` only touches nodes *in the current projection* — a doc whose links disappeared since the last run isn't projected, so its old `themeId` would survive and pollute member counts. Wipe the vault first:

```cypher
MATCH (d:Document)
WHERE d.uri STARTS WITH $vaultPrefix AND d.themeId IS NOT NULL
REMOVE d.themeId
```

(Between 4a and 4b the vault briefly has no themes — same don't-query-mid-rebuild caveat as `ki index`.)

**4b. Persist the new assignment:**

```cypher
CALL gds.graph.nodeProperties.write($graphName, ['themeId'])
YIELD propertiesWritten
RETURN propertiesWritten
```

**4c. Fold sub-floor themes into ungrouped.** Themes with fewer than `min_theme_doc_count` (default **3**) member docs aren't themes — remove their `themeId` so those docs count as ungrouped. This is deliberately *not* Leiden's `minCommunitySize`: that floor counts projected nodes **including glue**, but ours is on `LOCAL_FILE` member docs — the count below uses the right unit:

```cypher
MATCH (m:Document {sourceType: 'LOCAL_FILE'})
WHERE m.uri STARTS WITH $vaultPrefix AND m.themeId IS NOT NULL
WITH m.themeId AS theme, count(m) AS docCount
WHERE docCount < $minThemeDocCount
WITH collect(theme) AS smallThemes
MATCH (d:Document)
WHERE d.uri STARTS WITH $vaultPrefix AND d.themeId IN smallThemes
REMOVE d.themeId
```

The second MATCH is label-wide on purpose — it strips folded themes' glue nodes too, not just members.

## 5. Cohesion word (from conductance)

**Thresholds, calibrated against real vaults. The output contract is only the three phrases, so these may move freely:**

| `cohesion` | Rule |
|---|---|
| `tight` | conductance ≤ 0.35 |
| `loose` | conductance ≥ 0.60 |
| `moderate` | everything else |

These replace an earlier 0.2 / 0.5 split that was theoretical. Measured against two independent real vaults, **that split produced zero `tight` themes** — a third of the vocabulary never appeared, which is worse than not having the word. Real document graphs leak more than the theory suggests: a genuinely coherent theme still cites its neighbours. Observed conductances across the calibration set were 0.067, 0.299, 0.333, 0.364, 0.420, 0.573, 0.620; the bands above split that spread across all three phrases. Re-check when a third vault shape (especially a journal-style one) is available.

Why conductance alone: it's per-community, computed in-memory next to the Leiden call (no post-write Cypher pass), not size-biased the way `n·(n−1)/2` edge density is (large themes are sparse by nature and would always read loose), and it runs on the full projection so co-citation glue counts toward cohesion. A singleton or barely-connected community conducts ~1.0 and correctly reads `loose`. (An earlier draft blended per-community modularity with a doc–doc edge-density Cypher query — both size-biased in opposite directions, and the density pass cost as much as the projection itself.)

## 6. Renderer-feeding queries (wire records per theme-format.md)

**Members + within-theme link counts** (drives `most-linked docs` order and `(+N more)`):

```cypher
MATCH (src)-[l:LINKS_TO]->(tgt)
WHERE src.uri STARTS WITH $vaultPrefix AND tgt.uri STARTS WITH $vaultPrefix
MATCH (s:Document {uri: split(src.uri, '#')[0]})
MATCH (t:Document {uri: split(tgt.uri, '#')[0]})
WHERE s.sourceType = 'LOCAL_FILE' AND t.sourceType = 'LOCAL_FILE' AND s <> t
  AND s.themeId IS NOT NULL AND s.themeId = t.themeId
UNWIND [s, t] AS d
WITH d.themeId AS theme, d, count(*) AS withinThemeLinks
RETURN theme, d.uri AS uri, d.displayName AS displayName, withinThemeLinks
ORDER BY theme, withinThemeLinks DESC, uri
```

**Top wikilink targets per theme** (`[[…]] in N docs` — counts *theme docs linking to the target*, target may be any node incl. stubs/unresolved):

```cypher
MATCH (src)-[l:LINKS_TO {wikilink: true}]->(tgt)
WHERE src.uri STARTS WITH $vaultPrefix
MATCH (s:Document {uri: split(src.uri, '#')[0]})
WHERE s.sourceType = 'LOCAL_FILE' AND s.themeId IS NOT NULL
  AND split(tgt.uri, '#')[0] <> s.uri          // self-links don't characterize a theme
WITH s.themeId AS theme, tgt, count(DISTINCT s) AS linkingDocs
ORDER BY theme, linkingDocs DESC, tgt.uri
WITH theme, collect({uri: tgt.uri, displayName: tgt.displayName, docs: linkingDocs})[..5] AS targets
RETURN theme, targets
```

**Crossover docs** (`links into T<j> via`):

```cypher
MATCH (src)-[l:LINKS_TO]->(tgt)
WHERE src.uri STARTS WITH $vaultPrefix AND tgt.uri STARTS WITH $vaultPrefix
MATCH (s:Document {uri: split(src.uri, '#')[0]})
MATCH (t:Document {uri: split(tgt.uri, '#')[0]})
WHERE s.sourceType = 'LOCAL_FILE' AND t.sourceType = 'LOCAL_FILE'
  AND s.themeId IS NOT NULL AND t.themeId IS NOT NULL
  AND s.themeId <> t.themeId
WITH s.themeId AS theme, t.themeId AS otherTheme, s, count(*) AS crossLinks
ORDER BY theme, otherTheme, crossLinks DESC, s.uri
WITH theme, otherTheme, collect({uri: s.uri, displayName: s.displayName})[0] AS via
RETURN theme, otherTheme, via
```

**Header scalars:**

```cypher
MATCH (d:Document {sourceType: 'LOCAL_FILE'})
WHERE d.uri STARTS WITH $vaultPrefix
RETURN count(d) AS totalDocs,
       count(d.themeId) AS groupedDocs
```

## 7. Statement order

The pipeline lives in `src/ki/themes/producer.py` (`produce()`); the Cypher constants it runs are in `src/ki/themes/queries.py`, named to match the sections above. Nine statements, in this order — the ordering is load-bearing at three points, flagged below:

| # | Statement | Constant | Note |
|---|-----------|----------|------|
| 1 | `RETURN gds.version()` | `GDS_VERSION` | Capability probe. Fails fast with a capability error before any work. |
| 2 | `gds.graph.drop($g, false)` | `DROP_GRAPH` | `failIfMissing = false`: no-op normally, **self-heal** when a previous run crashed before its own drop and left the named graph behind. |
| 3 | projection | `PROJECT` | §2. |
| 4 | `gds.leiden.mutate` | `LEIDEN_MUTATE` | §3. **Must precede 5** — the in-memory graph never sees DB writes, so `write` mode would leave the projection without `themeId` and break the metric. |
| 5 | `gds.conductance.stream` | `CONDUCTANCE` | §4. Reads the mutated property. |
| 6 | clear stale `themeId` | `CLEAR_STALE` | §4a. **Must precede 7** — `nodeProperties.write` only touches projected nodes, so a doc whose links vanished would keep a stale id. |
| 7 | `gds.graph.nodeProperties.write` | `NODE_PROPERTIES_WRITE` | §4b. |
| 8 | fold sub-floor themes | `FOLD_SMALL_THEMES` | §4c. **Must follow 7** — it counts persisted member docs. |
| 9 | `gds.graph.drop($g, false)` | `DROP_GRAPH` | In a `finally`, so a mid-pipeline failure still releases the projection. |

Then the four read-only renderer queries of §6 (`MEMBERS`, `TOP_TARGETS`, `CROSSOVERS`, `HEADER_SCALARS`) plus `VAULT_NAME`, and the producer assembles the wire records.

Steps 6–7 leave the vault briefly themeless — the same "don't query mid-rebuild" caveat as `ki index`. A crash in that window leaves docs **missing** `themeId`, never holding wrong ones, and the next run self-heals.

## Tested against a real vault

Re-verified through the shipped `ki theme` against `content-research-wiki` (91 docs, 940 links; local Podman Neo4j 2026.04.0 with GDS 2026.04.0). Result: 73/91 grouped into 5 themes at defaults (granularity 1.0, floor 3), all coherent on eyeball (AIP content / blog concepts / ki-feedback / wiki-feedback / workshops), whole-run modularity 0.350. Fold, stale-clear, write-back, and projection drop all verified in the DB afterward. Findings:

- **`Document.displayName` is the filename today** (per data-model "for now"), so the binds line renders `[[aip-paper.md]]` rather than pretty link text. Format survives; improves automatically if displayName ever carries H1/link text.
- **No theme read `tight`** under the original 0.2/0.5 thresholds — T1–T3 moderate, T4–T5 loose. **Resolved:** thresholds recalibrated to 0.35/0.60 (see §5); the same vault now reads T1 tight, T2–T3 moderate, T4 loose.
- **Hub docs dominate crossover picks** — `CLAUDE.md` / `index.md` were the "via" doc for most theme pairs. **Resolved via `--exclude`:** rerunning with `--exclude CLAUDE.md --exclude index.md` produced meaningful via-docs *and* better communities (one theme grew 11→14 docs — the hub edges were distorting Leiden itself, which is why exclusion filters the projection, not just the output).
- **Co-citation clustering is verified** by `tests/integration/test_theme.py`, whose fixture is two stars of documents that *never link each other* and share only a common target. They cluster correctly, which is the design's central claim.
- **`WIKILINK_UNRESOLVED` glue nodes do not exist in ki today** — see *Notes / open items*. The `LOCAL_STUB` half of the glue path does work (a shared attachment clusters the docs that link it) and is covered by the same test.

## Notes / open items

- **Small-theme floor: resolved.** `min_theme_doc_count` (default 3) folds sub-floor themes into ungrouped at step 4c. Deliberately not Leiden's `minCommunitySize` — that counts projected nodes including glue; ours counts `LOCAL_FILE` member docs.
- **Null `themeId` handling: verified.** Members/crossovers filter via `s.themeId = t.themeId` (null propagates → row dropped) plus explicit `IS NOT NULL`; the header's `count(d.themeId)` skips nulls. Docs cleared by 4a/4c land in ungrouped with no further handling.
- **Concurrent producers (two agents, same vault): no locking needed.** The deterministic projection name (`ki-theme-<vault>`) is a de-facto mutex — a second concurrent run fails at `gds.graph.project` with "graph already exists" before touching any `themeId`. Races that slip past it are benign: seeded `concurrency=1` runs are deterministic, so concurrent writers produce *identical* values and interleavings converge; the worst case (one run clears via 4a then crashes before 4b) leaves docs **missing** themeIds, never wrong ones, and the next run self-heals. Readers mid-rebuild inherit the same "don't query during re-index" caveat as `ki index`. One operational consequence: a crashed run leaves the named graph behind — drop it (`gds.graph.drop`) before retrying.
- **Multi-user: no namespacing needed.** `themeId` is *vault*-derived, not user-derived: the seeded, `concurrency=1` Leiden run is deterministic, so two users producing themes on the same vault converge to identical assignments rather than colliding. Across vaults, raw integer ids can repeat, but every theme query is vault-prefix-scoped, so they never mix. Contract: `themeId` is meaningful only within (vault, producer run) — same rule as theme numbering in theme-format.md. If per-user theme *parameters* (different gamma per user on a shared graph) ever become real, that's per-user property names or separate profiles — out of scope now.
- **`themeId` write is destructive-ish state** — overwritten per producer run; `ki drop`/`ki index` rebuild paths must not treat it as user data (it isn't — ki-owned, like `uri`).
- **No folder fallback — resolved, and deliberately so.** Grouping by directory hierarchy answers a different question than grouping by interlinking, so it is not a "degraded" version of this; it would be a different command. GDS is a hard requirement and a GDS-less profile gets a capability error ([#72](https://github.com/zach-blumenfeld/knowledge-index/issues/72)).
- **⚠️ `WIKILINK_UNRESOLVED` nodes are never created by ki today.** The projection admits them and this doc describes them as the co-citation carrier for journal-style vaults, but `_emit_link` in `src/ki/ingest/pipeline.py` drops a wikilink whose target doesn't resolve (`if target is None: return`) — no node, no edge. So the glue path currently rests on `LOCAL_STUB` only. **Consequence:** in a vault where people write `[[concept]]` with no backing file, those links contribute *nothing* to theme formation — exactly the vault shape the glue design was aimed at. Co-citation via *resolved* targets works fine and is the common case in a wiki-style vault. Keeping the label in the projection query costs nothing and makes `ki theme` correct the day ingest starts materializing them. This is an **ingest-side gap, not a theme-side one**; it needs its own issue.
- **Determinism caveat:** `random_seed` with `concurrency=1` makes Leiden runs repeatable on the same projection; a changed graph can still relabel communities — consistent with the "theme ids stable only within a single run" contract.
- **`exemplar_pos` dropped from the wire contract.** An earlier draft carried it on member rows, always null, so a producer could override exemplar order. Nothing ever set it and the renderer derives the order from `count` (within-theme links) per theme-format.md *Ordering*; a field that is always null is a field that drifts. Re-add it the day a producer actually needs it.
- **Explored and deferred: co-citation collapse (bipartite projection).** Instead of glue nodes, materialize direct doc–doc edges weighted by *shared link targets* (two docs each citing `[[GraphRAG]]` get an edge), keeping the projection homogeneous — modularity-based clustering is more principled on a unipartite graph. Not done now because of **quadratic blowup around popular targets**: a concept cited by *k* docs expands to *k·(k−1)/2* edges (one `[[ki]]` tag on 200 journal entries → ~20k edges), while the glue-node form carries the same connectivity in *k* edges. Revisit if community quality on the docs+glue projection disappoints — and if so, cap or TF-IDF-downweight high-degree targets before collapsing.
