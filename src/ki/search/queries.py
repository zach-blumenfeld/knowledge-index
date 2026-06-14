"""Retrieval queries, lifted from docs/data-model/retrieval-queries.md.

v1 sign-off requires B.1, B.2, B.3 to be reachable via `ki search` flags.
B.4–B.10 ship as constants so they're easy to wire up later.
"""

from __future__ import annotations

INDEX_NAME = "content_search"


# B.1 — Document title fulltext.
B1_DOCUMENT_TITLE = """
CALL db.index.fulltext.queryNodes($index_name, $query)
YIELD node, score
WHERE node:Document
RETURN node.uri AS document_uri,
       node.displayName AS title,
       node.path AS path,
       score
ORDER BY score DESC
LIMIT toInteger($k)
""".strip()


# B.2 — Section content fulltext.
B2_SECTION_CONTENT = """
CALL db.index.fulltext.queryNodes($index_name, $query)
YIELD node, score
WITH node AS section, score
WHERE section:Section
WITH section, score
ORDER BY score DESC
LIMIT toInteger($k)
MATCH (doc:Document)-[:HAS*]->(section)
RETURN doc.uri AS document_uri,
       doc.displayName AS document_title,
       section.uri AS section_uri,
       section.displayName AS heading,
       section.headingLevel AS heading_level,
       section.content AS content,
       section.path AS path,
       score
""".strip()


# B.11 — Vault fulltext (`name` + `displayName` + `description`). Same shared
# `content_search` index, filtered to :Vault. Returns the vault URI so callers
# can render a helpful list and (once #17's `--under` lands) scope subsequent
# searches to the chosen vault. No CLI-side scoping flag exists today.
B11_VAULT_SEARCH = """
CALL db.index.fulltext.queryNodes($index_name, $query)
YIELD node, score
WHERE node:Vault
RETURN node.uri AS vault_uri,
       node.name AS name,
       node.displayName AS display_name,
       node.path AS path,
       node.description AS description,
       score
ORDER BY score DESC
LIMIT toInteger($k)
""".strip()


# B.3 — Document neighbourhood. The `{1,$n}` quantifier is a placeholder —
# Neo4j 5.x (including current Aura) rejects Cypher parameters inside
# quantified-path-pattern quantifiers, so `run_b3` substitutes the integer
# literal client-side before sending the query. Legacy `[:LINKS_TO*1..n]`
# syntax has the same parameter restriction *and* requires literal n at
# parse time, so we'd have to substitute either way.
#
# Known limitation: the chain walks pure `:LINKS_TO` edges, but the parser
# emits Doc→Doc `LINKS_TO` only for *preamble* wikilinks. Wikilinks inside a
# section produce Section→Doc edges, and the landed-on Document has no
# outgoing `LINKS_TO` to extend the chain — so multi-hop traversal stops
# at distance 1 for section-internal links. Fixing this would require
# interleaving HAS hops inside the LINKS_TO chain. Tracked separately.
B3_NEIGHBOURHOOD = """
MATCH (start:Document {uri: $uri})
MATCH (start)-[:HAS*0..]->(startElem)
MATCH linkPath = (startElem) (()-[:LINKS_TO]->()){1,$n} (endElem)
WITH endElem, length(linkPath) AS distance
OPTIONAL MATCH (endDoc:Document)-[:HAS*]->(endElem)
WHERE endElem:Section
WITH coalesce(endDoc, endElem) AS neighbour, distance
WHERE neighbour:Document AND neighbour.uri <> $uri
WITH neighbour, min(distance) AS distance
RETURN neighbour.uri AS document_uri,
       neighbour.displayName AS title,
       distance
ORDER BY distance, document_uri
""".strip()


def run_b1(session, query: str, k: int = 10) -> list[dict]:
    # `query` clashes with Session.run's first positional, so pass via dict.
    res = session.run(
        B1_DOCUMENT_TITLE,
        parameters={"index_name": INDEX_NAME, "query": query, "k": k},
    )
    return [dict(r) for r in res]


def run_b2(session, query: str, k: int = 10) -> list[dict]:
    res = session.run(
        B2_SECTION_CONTENT,
        parameters={"index_name": INDEX_NAME, "query": query, "k": k},
    )
    return [dict(r) for r in res]


def run_b3(session, doc_uri: str, n: int = 2) -> list[dict]:
    # Neo4j 5.x (incl. current Aura) rejects Cypher parameters inside
    # quantified-path-pattern quantifiers — substitute the literal int into
    # the query string client-side. Safe because `n` is coerced to int first.
    n_int = max(1, int(n))
    query = B3_NEIGHBOURHOOD.replace("$n", str(n_int))
    res = session.run(query, parameters={"uri": doc_uri})
    return [dict(r) for r in res]


def run_vault_search(session, query: str, k: int = 10) -> list[dict]:
    res = session.run(
        B11_VAULT_SEARCH,
        parameters={"index_name": INDEX_NAME, "query": query, "k": k},
    )
    return [dict(r) for r in res]


# Unified content search over Document + Section — the `ki search` default.
# Uses the same shared `content_search` index, which searches ALL indexed
# fields at once (displayName + content + aliases + description); the B1/B2
# constants above are *not* title-only / content-only despite their names.
# Optional structural filters, both controlled by us (not the user's query):
#   - $labels: restrict to a subset of {"Document","Section"} (the --types flag)
#   - $prefix: a `<Vault.uri>/` prefix to scope to one vault's subtree.
# The uri scope is a Cypher property predicate (exact path prefix), NOT a
# Lucene clause: `content_search` uses the standard analyzer, which would
# tokenize/shred a uri, so prefix-matching must run here on the stored value.
SEARCH_DOC_SECTION = """
CALL db.index.fulltext.queryNodes($index_name, $query) YIELD node, score
WHERE (node:Document OR node:Section)
  AND ($labels IS NULL OR any(l IN labels(node) WHERE l IN $labels))
  AND ($prefix IS NULL OR node.uri STARTS WITH $prefix)
WITH node, score
ORDER BY score DESC
LIMIT toInteger($k)
OPTIONAL MATCH (doc:Document)-[:HAS*]->(node)
RETURN
  CASE WHEN node:Section THEN 'Section' ELSE 'Document' END AS label,
  node.uri          AS uri,
  node.displayName  AS display_name,
  node.path         AS path,
  node.content      AS content,
  doc.uri           AS document_uri,
  doc.displayName   AS document_title,
  score
""".strip()


def run_search(
    session,
    query: str,
    *,
    vault_prefix: str | None = None,
    labels: list[str] | None = None,
    k: int = 10,
) -> list[dict]:
    """Unified fulltext over Document + Section, optionally scoped.

    `labels` restricts to a subset of {"Document", "Section"} (None = both).
    `vault_prefix` (e.g. "my-notes/") scopes results to one vault's subtree.
    Sections carry their owning Document's uri/title; Documents leave those null.
    """
    res = session.run(
        SEARCH_DOC_SECTION,
        parameters={
            "index_name": INDEX_NAME,
            "query": query,
            "labels": labels,
            "prefix": vault_prefix,
            "k": k,
        },
    )
    return [dict(r) for r in res]


# B.12 — Containment tree (HAS walk). `ki outline`'s hierarchy producer.
#
# Emits the wire record format defined in docs/commands/outline.md *Wire record
# format*: {depth, inrel, label, name, displayName, uri, parent_uri,
# sort_pos}. Sections carry sort_pos (NEXT_SECTION position in the parent
# doc) so the renderer can order sibling sections by reading order.
#
# When $root_uri is null, every :Vault in the graph is matched as a root —
# the renderer treats multi-root output as a sibling group at
# parent_uri=null, sorted alphabetically by name.
#
# Outbound :LINKS_TO edges are surfaced by B12_LINKS; this query is HAS-only.
#
# The `{1,$depth}` quantifier is a placeholder — same constraint as B.3,
# `run_b12` substitutes the integer literal client-side.
B12_CONTAINMENT_TREE = """
MATCH (root)
WHERE ($root_uri IS NOT NULL
       AND root.uri = $root_uri
       AND (root:Vault OR root:Folder OR root:Document OR root:Section))
   OR ($root_uri IS NULL AND root:Vault)

CALL (root) {
  RETURN 0                                       AS depth,
         null                                    AS inrel,
         labels(root)[0]                         AS label,
         coalesce(root.name, root.displayName)   AS name,
         root.displayName                        AS displayName,
         root.uri                                AS uri,
         null                                    AS parent_uri,
         null                                    AS sort_pos

  UNION

  MATCH path = (root) (()-[:HAS]->()){1,$depth} (d)
  OPTIONAL MATCH nsp = (firstSec:Section)-[:NEXT_SECTION*0..]->(d)
  WHERE d:Section
    AND NOT EXISTS { MATCH (:Section)-[:NEXT_SECTION]->(firstSec) }
  RETURN length(path)                            AS depth,
         'HAS'                                   AS inrel,
         labels(d)[0]                            AS label,
         coalesce(d.name, d.displayName)         AS name,
         d.displayName                           AS displayName,
         d.uri                                   AS uri,
         nodes(path)[-2].uri                     AS parent_uri,
         CASE WHEN d:Section THEN length(nsp) ELSE null END AS sort_pos
}

RETURN depth, inrel, label, name, displayName, uri, parent_uri, sort_pos
""".strip()


# B.12-links — outbound :LINKS_TO edges from a set of source URIs. Called
# by `ki outline` after B.12 to surface horizontal LINKS_TO branches. The
# renderer combines these rows with B.12 hierarchy rows, sets
# `depth = source_depth + 1` and `inrel = 'LINKS_TO'`, and sorts L
# siblings alphabetically by target uri.
B12_LINKS = """
UNWIND $source_uris AS source_uri
MATCH (src {uri: source_uri})-[:LINKS_TO]->(tgt)
WHERE src:Document OR src:Section
RETURN src.uri                              AS parent_uri,
       labels(tgt)[0]                       AS label,
       coalesce(tgt.name, tgt.displayName)  AS name,
       tgt.displayName                      AS displayName,
       tgt.uri                              AS uri
ORDER BY parent_uri, uri
""".strip()


def run_b12(session, root_uri: str | None, depth: int = 4) -> list[dict]:
    # Neo4j 5.x rejects Cypher parameters inside quantified-path quantifiers —
    # substitute the literal int into the query string client-side. Safe
    # because `depth` is coerced to int first. Same trick as `run_b3`.
    depth_int = max(1, int(depth))
    query = B12_CONTAINMENT_TREE.replace("$depth", str(depth_int))
    res = session.run(query, parameters={"root_uri": root_uri})
    return [dict(r) for r in res]


def run_b12_links(session, source_uris: list[str]) -> list[dict]:
    if not source_uris:
        return []
    res = session.run(B12_LINKS, parameters={"source_uris": list(source_uris)})
    return [dict(r) for r in res]


# B.4 — Document text in reading order. Used by `ki get --type full` on
# a `:Document` URI. Walks the NEXT_SECTION chain from the doc's first
# section to its last; defensive `(start)-[:HAS*]->(section)` keeps the
# walk inside this document.
B4_DOCUMENT_TEXT = """
MATCH (start:Document {uri: $uri})-[:HAS]->(first:Section)
WHERE NOT (:Section)-[:NEXT_SECTION]->(first)
MATCH path = (first)-[:NEXT_SECTION*0..]->(section:Section)
WHERE (start)-[:HAS*]->(section)
RETURN section.uri AS section_uri,
       section.displayName AS heading,
       section.headingLevel AS heading_level,
       section.content AS content,
       length(path) AS reading_order
ORDER BY reading_order
""".strip()


# B.13 — Node lookup. `ki get`'s metadata reader. Returns `label` plus
# the node's property bag — naming label-optional properties (frontmatter,
# aliases, ...) directly in Cypher would trigger Neo4j's `01N52`
# "property does not exist" notification on small vaults where no node
# has ever been written with that key. `properties(n)` returns only the
# keys that actually exist on the node. The Python wrapper flattens
# `props` into the top level so callers see `row["frontmatter"]` /
# `row.get("frontmatter")` exactly as before.
B13_NODE_LOOKUP = """
MATCH (n {uri: $uri})
RETURN labels(n)[0] AS label, properties(n) AS props
""".strip()


# B.14 — Section text with subtree. Used by `ki get --type full` on a
# `:Section` URI. Walks NEXT_SECTION from the start section, bounded to
# the subtree under start via `start = s OR (start)-[:HAS*]->(s)`.
B14_SECTION_SUBTREE = """
MATCH (start:Section {uri: $uri})
MATCH path = (start)-[:NEXT_SECTION*0..]->(s:Section)
WHERE start = s OR (start)-[:HAS*]->(s)
RETURN s.uri          AS section_uri,
       s.displayName  AS heading,
       s.headingLevel AS heading_level,
       s.content      AS content,
       length(path)   AS reading_order
ORDER BY reading_order
""".strip()


def run_b4(session, doc_uri: str) -> list[dict]:
    res = session.run(B4_DOCUMENT_TEXT, parameters={"uri": doc_uri})
    return [dict(r) for r in res]


def run_b13(session, uri: str) -> dict | None:
    """Single-row node lookup.

    Flattens the Cypher `{label, props}` shape into a single dict so
    callers see `row["uri"]`, `row.get("frontmatter")`, etc. — same
    interface as if the columns had been named explicitly. Returns None
    if the URI doesn't resolve.
    """
    res = session.run(B13_NODE_LOOKUP, parameters={"uri": uri})
    rows = [dict(r) for r in res]
    if not rows:
        return None
    row = rows[0]
    props = row.get("props") or {}
    return {**props, "label": row.get("label")}


def run_b14(session, section_uri: str) -> list[dict]:
    res = session.run(B14_SECTION_SUBTREE, parameters={"uri": section_uri})
    return [dict(r) for r in res]
