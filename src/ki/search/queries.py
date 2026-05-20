"""Retrieval queries, lifted from docs/retrieval-queries.md.

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
