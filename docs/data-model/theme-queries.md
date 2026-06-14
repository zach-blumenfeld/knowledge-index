# Theme producer — GDS queries + pipeline script

> **⚠️ Status: DRAFT — not yet implemented.** Producer side of `docs/commands/theme-format.md`. No CLI integration yet — there is no `ki theme` command; the pipeline below runs only via the standalone `scripts/theme_producer.py`. This documents the Cypher + GDS pipeline that emits the wire records the theme renderer consumes. Requires the **GDS plugin** (Leiden ships in GDS ≥ 2.x; not available on Aura Free) and the `graphdatascience` Python client (`uv add graphdatascience`).

Pipeline: **project doc-level link graph (docs + glue nodes) → Leiden (mutate `themeId`) → per-community conductance → write `themeId` → renderer queries → drop projection.**

> **Runnable version: `scripts/theme_producer.py`** — same pipeline as the embedded script below, plus ki-style vault/profile resolution (`.ki/vault.yaml` → `~/.config/ki/config.yaml`), a friendly GDS-missing error, and a theme-format renderer (`--json` for raw wire records). Run from a vault directory:
> `uv run --with graphdatascience python scripts/theme_producer.py [vault-path] [--gamma 1.0] [--min-docs 3] [--exclude CLAUDE.md] [--top-k 5] [--json]`

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

```python
G, _ = gds.graph.cypher.project(
    """
    MATCH (src)-[l:LINKS_TO]->(tgt)
    WHERE src.uri STARTS WITH $vaultPrefix
      AND tgt.uri STARTS WITH $vaultPrefix
    MATCH (s:Document {uri: split(src.uri, '#')[0]})
    MATCH (t:Document {uri: split(tgt.uri, '#')[0]})
    WHERE s.sourceType = 'LOCAL_FILE'
      AND t.sourceType IN ['LOCAL_FILE', 'LOCAL_STUB', 'WIKILINK_UNRESOLVED']
      AND s <> t
    WITH s, t, count(*) AS weight
    RETURN gds.graph.project(
      $graph_name, s, t,
      { relationshipProperties: { weight: weight } },
      { undirectedRelationshipTypes: ['*'] }
    )
    """,
    graph_name=graph_name,
    vaultPrefix=vault_prefix,
)
```

This is the §1 match with the target filter relaxed to admit **glue nodes** (`LOCAL_STUB`, `WIKILINK_UNRESOLVED`) — see the step-by-step above. Docs with no links at all never enter the projection — they come out as **ungrouped** (header `<U>`), computed in step 6 as `total_docs − grouped`. No WCC pre-pass needed: Leiden handles a disconnected projection (each component clusters independently).

## 3. Leiden → `themeId` (mutate, not write)

```python
res = gds.v2.leiden.mutate(
    G,
    mutate_property="themeId",
    relationship_weight_property="weight",
    gamma=1.0,                 # resolution; >1 → more, smaller themes
    random_seed=42,
    concurrency=1,             # required for the seed to make runs deterministic
)
# res.community_count, res.modularity (overall — sanity: > 0.3 means real structure)
```

**`mutate`, deliberately not `write`:** §4's conductance metric reads `themeId` from the *in-memory* graph, and the projection never sees DB writes — so a one-step `leiden.write` would persist the property but leave the projection without it, breaking §4. Mutate first; persistence to Neo4j happens in §4 via `gds.v2.graph.node_properties.write(G, ["themeId"])` after the metric runs.

`themeId` lands on every projected node — `LOCAL_FILE` docs *and* glue nodes. That's fine: membership queries filter to `LOCAL_FILE` at read time, and a glue node's `themeId` is potentially useful later (e.g. tagging `top wikilink targets` with their home theme). It is index-generation state, owned by ki (not user-authored — consistent with design principle #1); a re-index that reruns the producer overwrites it.

## 4. Per-community conductance (cohesion input)

```python
cond_df = gds.v2.conductance.stream(
    G,
    community_property="themeId",        # property must be in the projection — see note
    relationship_weight_property="weight",
)
# columns: community, conductance — fraction of the community's link mass
# that crosses its boundary. 0 = fully self-contained, →1 = mostly leaks out.
```

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

```python
gds.v2.graph.node_properties.write(G, ["themeId"])
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

**Starting thresholds — tune against real vaults; the output contract is only the three phrases:**

| `cohesion` | Rule |
|---|---|
| `tight` | conductance ≤ 0.2 |
| `loose` | conductance ≥ 0.5 |
| `moderate` | everything else |

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

## 7. Pipeline script

```python
"""Theme producer: Leiden over the doc-level wikilink graph of one vault.

Usage:  uv run python theme_producer.py <vault-uri> [gamma] [min-theme-doc-count]
        gamma: Leiden resolution, default 1.0 — >1 → more, smaller themes
        min-theme-doc-count: themes with fewer member docs fold into ungrouped (default 3)
Env:    NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD  (the vault's ki profile)
Output: wire records per docs/commands/theme-format.md, as JSON on stdout.
"""

import json
import os
import sys

from graphdatascience import GraphDataScience

PAIR_MATCH = """
MATCH (src)-[l:LINKS_TO]->(tgt)
WHERE src.uri STARTS WITH $vaultPrefix AND tgt.uri STARTS WITH $vaultPrefix
MATCH (s:Document {uri: split(src.uri, '#')[0]})
MATCH (t:Document {uri: split(tgt.uri, '#')[0]})
WHERE s.sourceType = 'LOCAL_FILE' AND t.sourceType = 'LOCAL_FILE' AND s <> t
"""

# Projection variant: target side also admits glue nodes (co-citation signal).
PROJECTION_MATCH = """
MATCH (src)-[l:LINKS_TO]->(tgt)
WHERE src.uri STARTS WITH $vaultPrefix AND tgt.uri STARTS WITH $vaultPrefix
MATCH (s:Document {uri: split(src.uri, '#')[0]})
MATCH (t:Document {uri: split(tgt.uri, '#')[0]})
WHERE s.sourceType = 'LOCAL_FILE'
  AND t.sourceType IN ['LOCAL_FILE', 'LOCAL_STUB', 'WIKILINK_UNRESOLVED']
  AND s <> t
"""

COHESION_COND_TIGHT, COHESION_COND_LOOSE = 0.2, 0.5


def cohesion(conductance: float) -> str:
    if conductance <= COHESION_COND_TIGHT:
        return "tight"
    if conductance >= COHESION_COND_LOOSE:
        return "loose"
    return "moderate"


def main(vault_uri: str, gamma: float = 1.0, min_theme_doc_count: int = 3) -> None:
    vault_prefix = vault_uri.rstrip("/") + "/"
    graph_name = f"ki-theme-{vault_uri.replace('/', '-')}"
    gds = GraphDataScience(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    )

    # 1. Project: undirected, weighted, docs + glue nodes
    G, _ = gds.graph.cypher.project(
        PROJECTION_MATCH
        + """
        WITH s, t, count(*) AS weight
        RETURN gds.graph.project(
          $graph_name, s, t,
          { relationshipProperties: { weight: weight } },
          { undirectedRelationshipTypes: ['*'] }
        )
        """,
        graph_name=graph_name,
        vaultPrefix=vault_prefix,
    )

    try:
        # 2. Leiden (mutate first — conductance metric needs the property in-memory)
        gds.v2.leiden.mutate(
            G,
            mutate_property="themeId",
            relationship_weight_property="weight",
            gamma=gamma,
            random_seed=42,
            concurrency=1,
        )

        # 3. Per-community conductance → cohesion word
        cond_df = gds.v2.conductance.stream(
            G, community_property="themeId", relationship_weight_property="weight"
        )
        coh = {r.community: cohesion(r.conductance) for r in cond_df.itertuples()}

        # 4a. Clear stale themeIds from prior runs (write only touches projected nodes)
        gds.run_cypher(
            """
            MATCH (d:Document)
            WHERE d.uri STARTS WITH $vaultPrefix AND d.themeId IS NOT NULL
            REMOVE d.themeId
            """,
            params={"vaultPrefix": vault_prefix},
        )

        # 4b. Persist themeId to Neo4j
        gds.v2.graph.node_properties.write(G, ["themeId"])
    finally:
        gds.v2.graph.drop(G)

    # 4c. Fold sub-floor themes (member-doc count, not projected-node count) into ungrouped
    gds.run_cypher(
        """
        MATCH (m:Document {sourceType: 'LOCAL_FILE'})
        WHERE m.uri STARTS WITH $vaultPrefix AND m.themeId IS NOT NULL
        WITH m.themeId AS theme, count(m) AS docCount
        WHERE docCount < $minThemeDocCount
        WITH collect(theme) AS smallThemes
        MATCH (d:Document)
        WHERE d.uri STARTS WITH $vaultPrefix AND d.themeId IN smallThemes
        REMOVE d.themeId
        """,
        params={"vaultPrefix": vault_prefix, "minThemeDocCount": min_theme_doc_count},
    )

    # 5. Wire records
    members = gds.run_cypher(
        PAIR_MATCH
        + """
        AND s.themeId = t.themeId
        UNWIND [s, t] AS d
        WITH d.themeId AS theme, d, count(*) AS withinThemeLinks
        RETURN theme, d.uri AS uri, d.displayName AS displayName, withinThemeLinks
        ORDER BY theme, withinThemeLinks DESC, uri
        """,
        params={"vaultPrefix": vault_prefix},
    )
    targets = gds.run_cypher(
        """
        MATCH (src)-[l:LINKS_TO {wikilink: true}]->(tgt)
        WHERE src.uri STARTS WITH $vaultPrefix
        MATCH (s:Document {uri: split(src.uri, '#')[0]})
        WHERE s.sourceType = 'LOCAL_FILE' AND s.themeId IS NOT NULL
          AND split(tgt.uri, '#')[0] <> s.uri
        WITH s.themeId AS theme, tgt, count(DISTINCT s) AS linkingDocs
        ORDER BY theme, linkingDocs DESC, tgt.uri
        WITH theme,
             collect({uri: tgt.uri, displayName: tgt.displayName, docs: linkingDocs})[..5]
               AS targets
        RETURN theme, targets
        """,
        params={"vaultPrefix": vault_prefix},
    )
    crossovers = gds.run_cypher(
        PAIR_MATCH.replace("AND s <> t", "")
        + """
        AND s.themeId IS NOT NULL AND t.themeId IS NOT NULL AND s.themeId <> t.themeId
        WITH s.themeId AS theme, t.themeId AS otherTheme, s, count(*) AS crossLinks
        ORDER BY theme, otherTheme, crossLinks DESC, s.uri
        WITH theme, otherTheme, collect({uri: s.uri, displayName: s.displayName})[0] AS via
        RETURN theme, otherTheme, via
        """,
        params={"vaultPrefix": vault_prefix},
    )
    header = gds.run_cypher(
        """
        MATCH (d:Document {sourceType: 'LOCAL_FILE'})
        WHERE d.uri STARTS WITH $vaultPrefix
        RETURN count(d) AS totalDocs, count(d.themeId) AS groupedDocs
        """,
        params={"vaultPrefix": vault_prefix},
    ).iloc[0]

    rows = []
    for r in members.itertuples():
        rank = {"exemplar_pos": None}  # renderer assigns top-N exemplar positions
        rows.append({"cluster_key": str(r.theme), "kind": "member", "uri": r.uri,
                     "displayName": r.displayName, "count": int(r.withinThemeLinks),
                     "other_cluster_key": None,
                     "cohesion": coh.get(r.theme, "loose"), **rank})
    for r in targets.itertuples():
        for t in r.targets:
            rows.append({"cluster_key": str(r.theme), "kind": "link_target",
                         "uri": t["uri"], "displayName": t["displayName"],
                         "count": int(t["docs"]), "other_cluster_key": None,
                         "cohesion": None, "exemplar_pos": None})
    for r in crossovers.itertuples():
        rows.append({"cluster_key": str(r.theme), "kind": "crossover",
                     "uri": r.via["uri"], "displayName": r.via["displayName"],
                     "count": None, "other_cluster_key": str(r.otherTheme),
                     "cohesion": None, "exemplar_pos": None})

    print(json.dumps({
        "method": "links",
        "total_docs": int(header.totalDocs),
        "grouped_docs": int(header.groupedDocs),
        "rows": rows,
    }, indent=2))


if __name__ == "__main__":
    main(
        sys.argv[1],
        gamma=float(sys.argv[2]) if len(sys.argv) > 2 else 1.0,
        min_theme_doc_count=int(sys.argv[3]) if len(sys.argv) > 3 else 3,
    )
```

## Tested against a real vault (2026-06-12)

Ran verbatim against `content-research-wiki` (91 docs, 940 links; local Podman Neo4j 2026.04.0 recreated with `NEO4J_PLUGINS=["apoc","genai","graph-data-science"]` — GDS landed at 2026.04.0, data volume preserved). Result: 73/91 grouped into 5 themes (defaults: gamma 1.0, floor 3), all coherent on eyeball (AIP content / blog concepts / ki-feedback / wiki-feedback / workshops). Fold, stale-clear, write-back, and projection drop all verified in the DB afterward. Findings:

- **`Document.displayName` is the filename today** (per data-model "for now"), so the binds line renders `[[aip-paper.md]]` rather than pretty link text. Format survives; improves automatically if displayName ever carries H1/link text.
- **No theme read `tight`** — T1–T3 moderate, T4–T5 loose. Thresholds plausibly need loosening (or this vault is genuinely diffuse); tune with more vaults.
- **Hub docs dominate crossover picks** — `CLAUDE.md` / `index.md` were the "via" doc for most theme pairs. **Resolved via `--exclude`:** rerunning with `--exclude CLAUDE.md --exclude index.md` produced meaningful via-docs *and* better communities (one theme grew 11→14 docs — the hub edges were distorting Leiden itself, which is why exclusion filters the projection, not just the output).
- **Glue-node path untested here** — this vault has zero `LOCAL_STUB` / `WIKILINK_UNRESOLVED` nodes (every wikilink resolves). Needs a journal-style vault to validate co-citation clustering.

## Notes / open items

- **Small-theme floor: resolved.** `min_theme_doc_count` (default 3) folds sub-floor themes into ungrouped at step 4c. Deliberately not Leiden's `minCommunitySize` — that counts projected nodes including glue; ours counts `LOCAL_FILE` member docs.
- **Null `themeId` handling: verified.** Members/crossovers filter via `s.themeId = t.themeId` (null propagates → row dropped) plus explicit `IS NOT NULL`; the header's `count(d.themeId)` skips nulls. Docs cleared by 4a/4c land in ungrouped with no further handling.
- **Concurrent producers (two agents, same vault): no locking needed.** The deterministic projection name (`ki-theme-<vault>`) is a de-facto mutex — a second concurrent run fails at `gds.graph.project` with "graph already exists" before touching any `themeId`. Races that slip past it are benign: seeded `concurrency=1` runs are deterministic, so concurrent writers produce *identical* values and interleavings converge; the worst case (one run clears via 4a then crashes before 4b) leaves docs **missing** themeIds, never wrong ones, and the next run self-heals. Readers mid-rebuild inherit the same "don't query during re-index" caveat as `ki index`. One operational consequence: a crashed run leaves the named graph behind — drop it (`gds.graph.drop`) before retrying.
- **Multi-user: no namespacing needed.** `themeId` is *vault*-derived, not user-derived: the seeded, `concurrency=1` Leiden run is deterministic, so two users producing themes on the same vault converge to identical assignments rather than colliding. Across vaults, raw integer ids can repeat, but every theme query is vault-prefix-scoped, so they never mix. Contract: `themeId` is meaningful only within (vault, index generation) — same rule as theme numbering in theme-format.md. If per-user theme *parameters* (different gamma per user on a shared graph) ever become real, that's per-user property names or separate profiles — out of scope now.
- **`themeId` write is destructive-ish state** — overwritten per producer run; `ki drop`/`ki index` rebuild paths must not treat it as user data (it isn't — ki-owned, like `uri`).
- **Folder fallback** (`method: folders` in the header) is not in this script — it's plain Cypher over `HAS`, no GDS; add when wiring `ki theme` so GDS-less profiles degrade per theme-format.md.
- **Determinism caveat:** `random_seed` with `concurrency=1` makes Leiden runs repeatable on the same projection; a changed graph can still relabel communities — consistent with the "theme ids stable only within one index generation" contract.
- Member rows carry `exemplar_pos: null`; the renderer derives exemplar order from `count` (within-theme links) per theme-format.md *Ordering* — the field exists in the wire contract for producers that want to override.
- **Explored and deferred: co-citation collapse (bipartite projection).** Instead of glue nodes, materialize direct doc–doc edges weighted by *shared link targets* (two docs each citing `[[GraphRAG]]` get an edge), keeping the projection homogeneous — modularity-based clustering is more principled on a unipartite graph. Not done now because of **quadratic blowup around popular targets**: a concept cited by *k* docs expands to *k·(k−1)/2* edges (one `[[ki]]` tag on 200 journal entries → ~20k edges), while the glue-node form carries the same connectivity in *k* edges. Revisit if community quality on the docs+glue projection disappoints — and if so, cap or TF-IDF-downweight high-degree targets before collapsing.
