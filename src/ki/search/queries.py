"""Retrieval queries, lifted from docs/retrieval-queries.md.

v1 sign-off requires B.1, B.2, B.3 to be reachable via `ki search` flags.
B.4–B.10 ship as constants so they're easy to wire up later.
"""

from __future__ import annotations

INDEX_NAME = "doc_section_search"


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
MATCH (doc:Document)-[:HAS_SECTION*]->(section)
RETURN doc.uri AS document_uri,
       doc.displayName AS document_title,
       section.uri AS section_uri,
       section.displayName AS heading,
       section.headingLevel AS heading_level,
       section.content AS content,
       score
""".strip()


# B.3 — Document neighbourhood (quantified path pattern lets us parameterize
# the upper bound — legacy `[:LINKS_TO*1..$n]` syntax rejects parameters).
B3_NEIGHBOURHOOD = """
MATCH (start:Document {uri: $uri})
MATCH (start)-[:HAS_SECTION*0..]->(startElem)
MATCH linkPath = (startElem) (()-[:LINKS_TO]->()){1,$n} (endElem)
WITH endElem, length(linkPath) AS distance
OPTIONAL MATCH (endDoc:Document)-[:HAS_SECTION*]->(endElem)
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
    # `n` is bounded server-side via the quantified path pattern bound.
    res = session.run(B3_NEIGHBOURHOOD, parameters={"uri": doc_uri, "n": int(n)})
    return [dict(r) for r in res]
