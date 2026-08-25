"""Theme producer Cypher, lifted from docs/data-model/theme-queries.md.

Kept in lockstep with that doc (AGENTS.md *Conventions* — spec doc and
implementation change together).

Everything here is plain Cypher over the stock driver: the GDS procedures
are called directly (`CALL gds.leiden.mutate(...) YIELD ...`) rather than
through the `graphdatascience` Python client, which would drag pandas and
pyarrow into a CLI whose entire dependency set is seven small packages.
Procedure signatures verified against GDS 2026.04.0.

Two match prefixes drive everything:

- `PROJECTION_MATCH` — the *relaxed* form. The target side admits
  `LOCAL_STUB` / `WIKILINK_UNRESOLVED` **glue nodes** so that two docs
  citing the same `[[concept]]` cluster together even when they never link
  each other. That co-citation signal is most of what a journal-style vault
  has.
- `PAIR_MATCH` — the *strict* doc-to-doc form used by the renderer queries,
  where glue nodes would be noise (they are not members and never render).

Both collapse Section endpoints to their owning Document with
`split(uri, '#')[0]`, which is an equality match on `Document.uri` and so
hits the uniqueness constraint's index — much cheaper than walking `HAS*`
upward.
"""

from __future__ import annotations

import re


def _excluded(alias: str) -> str:
    """Exclusion predicate for `--exclude`: basename **or** vault-relative path.

    `$exclude` is lowercased client-side and ki URIs are already lowercase,
    so a `toLower` on the `name` side is all the normalization needed.
    """
    return (
        f"(toLower({alias}.name) IN $exclude"
        f" OR substring({alias}.uri, size($vaultPrefix)) IN $exclude)"
    )


# Strict doc-doc form — renderer queries (§6). Glue nodes excluded.
PAIR_MATCH = f"""
MATCH (src)-[l:LINKS_TO]->(tgt)
WHERE src.uri STARTS WITH $vaultPrefix AND tgt.uri STARTS WITH $vaultPrefix
MATCH (s:Document {{uri: split(src.uri, '#')[0]}})
MATCH (t:Document {{uri: split(tgt.uri, '#')[0]}})
WHERE s.sourceType = 'LOCAL_FILE' AND t.sourceType = 'LOCAL_FILE' AND s <> t
  AND NOT {_excluded('s')} AND NOT {_excluded('t')}
"""

# Relaxed form — projection (§2). Target side admits glue nodes.
PROJECTION_MATCH = f"""
MATCH (src)-[l:LINKS_TO]->(tgt)
WHERE src.uri STARTS WITH $vaultPrefix AND tgt.uri STARTS WITH $vaultPrefix
MATCH (s:Document {{uri: split(src.uri, '#')[0]}})
MATCH (t:Document {{uri: split(tgt.uri, '#')[0]}})
WHERE s.sourceType = 'LOCAL_FILE'
  AND t.sourceType IN ['LOCAL_FILE', 'LOCAL_STUB', 'WIKILINK_UNRESOLVED']
  AND s <> t
  AND NOT {_excluded('s')} AND NOT {_excluded('t')}
"""


# --- GDS capability probe -------------------------------------------------

# Cheap existence check. `gds.version()` is a *function*, so a missing plugin
# surfaces as a ClientError about an unknown function rather than a hang.
GDS_VERSION = "RETURN gds.version() AS version"


# --- §2 projection --------------------------------------------------------

# `gds.graph.project` used in its aggregation-function form: called once per
# row as pairs stream through, building the in-memory graph incrementally.
# `undirectedRelationshipTypes: ['*']` is required — Leiden is only defined
# on undirected graphs. Parallel weights sum on projection, so leaving the
# pairs directed (A→B w=3 and B→A w=2) is mathematically identical to one
# canonical edge of w=5; no canonical-ordering step is needed.
PROJECT = (
    PROJECTION_MATCH
    + """
WITH s, t, count(*) AS weight
RETURN gds.graph.project(
  $graphName, s, t,
  { relationshipProperties: { weight: weight } },
  { undirectedRelationshipTypes: ['*'] }
) AS g
"""
)


# --- §3 Leiden ------------------------------------------------------------

# `mutate`, not `write`: §4's conductance metric reads `themeId` off the
# in-memory graph, which never sees DB writes. Persistence happens after the
# metric, via NODE_PROPERTIES_WRITE.
#
# `randomSeed` + `concurrency: 1` together make the assignment deterministic
# for a given projection — the stability contract in theme-format.md.
LEIDEN_MUTATE = """
CALL gds.leiden.mutate($graphName, {
  mutateProperty: 'themeId',
  relationshipWeightProperty: 'weight',
  gamma: $gamma,
  randomSeed: 42,
  concurrency: 1
}) YIELD communityCount, modularity
RETURN communityCount, modularity
"""


# --- §4 conductance + write-back -----------------------------------------

# Fraction of each community's link mass that crosses its boundary.
# 0 = fully self-contained, →1 = mostly leaks out. Runs on the full
# projection, so co-citation glue counts toward cohesion.
CONDUCTANCE = """
CALL gds.conductance.stream($graphName, {
  communityProperty: 'themeId',
  relationshipWeightProperty: 'weight'
}) YIELD community, conductance
RETURN community, conductance
"""

# 4a — clear stale themeIds before writing. `nodeProperties.write` only
# touches nodes *in the current projection*, so a doc whose links vanished
# since the last run would otherwise keep its old themeId and pollute member
# counts. Vault-scoped (ki's DB is multi-vault) and label-wide within the
# vault so glue nodes are cleared too.
CLEAR_STALE = """
MATCH (n:Document)
WHERE n.uri STARTS WITH $vaultPrefix AND n.themeId IS NOT NULL
REMOVE n.themeId
"""

NODE_PROPERTIES_WRITE = """
CALL gds.graph.nodeProperties.write($graphName, ['themeId'])
YIELD propertiesWritten
RETURN propertiesWritten
"""

# 4c — fold sub-floor themes into ungrouped. Deliberately *not* Leiden's
# `minCommunitySize`, which counts projected nodes including glue; the floor
# is on LOCAL_FILE member docs, which is the unit the header reports.
# The second MATCH is label-wide so a folded theme's glue nodes go too.
FOLD_SMALL_THEMES = """
MATCH (m:Document {sourceType: 'LOCAL_FILE'})
WHERE m.uri STARTS WITH $vaultPrefix AND m.themeId IS NOT NULL
WITH m.themeId AS theme, count(m) AS docCount
WHERE docCount < $minThemeDocCount
WITH collect(theme) AS smallThemes
MATCH (n:Document)
WHERE n.uri STARTS WITH $vaultPrefix AND n.themeId IN smallThemes
REMOVE n.themeId
"""

# `failIfMissing = false` — makes the pre-run drop a no-op on the happy path
# and a self-heal when a previous run crashed between project and drop,
# leaving the named graph behind.
DROP_GRAPH = """
CALL gds.graph.drop($graphName, false) YIELD graphName
RETURN graphName
"""


# --- §6 renderer queries --------------------------------------------------

# Members + within-theme link counts. Drives `most-linked docs` ordering and
# the `(+N more docs)` remainder. `s.themeId = t.themeId` also filters nulls
# (null propagates, row drops), but the explicit IS NOT NULL documents it.
MEMBERS = (
    PAIR_MATCH
    + """
  AND s.themeId IS NOT NULL AND s.themeId = t.themeId
UNWIND [s, t] AS d
WITH d.themeId AS theme, d, count(*) AS withinThemeLinks
RETURN theme, d.uri AS uri, d.displayName AS displayName, withinThemeLinks
ORDER BY theme, withinThemeLinks DESC, uri
"""
)

# Top wikilink targets per theme — the naming evidence. Counts *theme docs
# linking to the target*, and the target may be any node including stubs and
# unresolved wikilinks (that is the whole point: `[[GraphRAG]]` with no file
# behind it is often the best label a theme has).
#
# `docs > 1` matters: a target only one doc cites is that doc's private
# reference, not something the theme converges on, so it is not evidence.
# Self-links are dropped — a doc citing its own sections says nothing.
TOP_TARGETS = """
MATCH (src)-[l:LINKS_TO {wikilink: true}]->(tgt)
WHERE src.uri STARTS WITH $vaultPrefix
MATCH (s:Document {uri: split(src.uri, '#')[0]})
WHERE s.sourceType = 'LOCAL_FILE' AND s.themeId IS NOT NULL
  AND split(tgt.uri, '#')[0] <> s.uri
  AND NOT (toLower(last(split(split(tgt.uri, '#')[0], '/'))) IN $exclude
           OR substring(split(tgt.uri, '#')[0], size($vaultPrefix)) IN $exclude)
WITH s.themeId AS theme, tgt,
     coalesce(tgt.displayName, tgt.name, tgt.uri) AS key,
     count(DISTINCT s) AS linkingDocs
WHERE linkingDocs > 1
ORDER BY theme, linkingDocs DESC, key
WITH theme, collect({uri: tgt.uri, displayName: key, docs: linkingDocs})[..5] AS targets
RETURN theme, targets
"""

# Crossover docs — one per (theme, otherTheme) pair: the drill handle for
# "how do these two themes connect". Note the `s <> t` guard is dropped from
# PAIR_MATCH here; the themeId inequality below already implies it.
CROSSOVERS = (
    PAIR_MATCH.replace("AND s <> t", "")
    + """
  AND s.themeId IS NOT NULL AND t.themeId IS NOT NULL
  AND s.themeId <> t.themeId
WITH s.themeId AS theme, t.themeId AS otherTheme, s, count(*) AS crossLinks
ORDER BY theme, otherTheme, crossLinks DESC, s.uri
WITH theme, otherTheme, collect({uri: s.uri, displayName: s.displayName})[0] AS via
RETURN theme, otherTheme, via
"""
)

# Header scalars. `count(d.themeId)` skips nulls, so grouped/ungrouped
# reconcile against totalDocs without a second pass.
HEADER_SCALARS = """
MATCH (d:Document {sourceType: 'LOCAL_FILE'})
WHERE d.uri STARTS WITH $vaultPrefix
WITH d, (toLower(d.name) IN $exclude
         OR substring(d.uri, size($vaultPrefix)) IN $exclude) AS excluded
RETURN count(CASE WHEN NOT excluded THEN d END) AS totalDocs,
       count(CASE WHEN NOT excluded THEN d.themeId END) AS groupedDocs,
       count(CASE WHEN excluded THEN d END) AS excludedDocs
"""

VAULT_NAME = """
MATCH (v:Vault {uri: $vaultUri})
RETURN coalesce(v.displayName, v.name, v.uri) AS name
"""


def graph_name_for(vault_uri: str) -> str:
    """Deterministic in-memory graph name for a vault.

    Deterministic on purpose: it doubles as a de-facto mutex. A second
    concurrent producer on the same vault fails at projection with "graph
    already exists" *before* touching any `themeId`, rather than interleaving
    writes with the first run.

    Vault URIs carry `:` and `/` (`vault://abc-123`), which GDS graph names
    reject, so everything outside `[A-Za-z0-9_-]` collapses to `-`.
    """
    return "ki-theme-" + re.sub(r"[^A-Za-z0-9_-]", "-", vault_uri)
