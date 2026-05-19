"""Cypher for ingest, lifted verbatim from docs/ingest-cypher.md.

If a query here drifts from the docs, fix the docs first then update the
implementation to match — the docs are the source of truth (see AGENTS.md
*Don't*).
"""

from __future__ import annotations

# 4.4 — Constraints and fulltext. Run once at first connect.
CONSTRAINT_USER = """
CREATE CONSTRAINT user_id_unique IF NOT EXISTS
  FOR (u:User) REQUIRE u.id IS UNIQUE
""".strip()

CONSTRAINT_VAULT = """
CREATE CONSTRAINT vault_uri_unique IF NOT EXISTS
  FOR (v:Vault) REQUIRE v.uri IS UNIQUE
""".strip()

CONSTRAINT_DOCUMENT = """
CREATE CONSTRAINT document_uri_unique IF NOT EXISTS
  FOR (d:Document) REQUIRE d.uri IS UNIQUE
""".strip()

CONSTRAINT_SECTION = """
CREATE CONSTRAINT section_uri_unique IF NOT EXISTS
  FOR (s:Section) REQUIRE s.uri IS UNIQUE
""".strip()

CONTENT_SEARCH_INDEX = """
CREATE FULLTEXT INDEX content_search IF NOT EXISTS
  FOR (n:Document|Section|Vault) ON EACH [n.displayName, n.content, n.aliases, n.description]
""".strip()

SCHEMA_STATEMENTS = (
    CONSTRAINT_USER,
    CONSTRAINT_VAULT,
    CONSTRAINT_DOCUMENT,
    CONSTRAINT_SECTION,
    CONTENT_SEARCH_INDEX,
)


# 4.3 — Per-vault-ingest write.
PER_VAULT_WRITE = """
MERGE (u:User {id: $userId})
ON CREATE SET u.firstSeenAt = $now
SET u += $userMutable,
    u.lastSeenAt = $now

MERGE (v:Vault {uri: $vaultUri})
ON CREATE SET v.firstSeenAt = $now
SET v += $vaultMutable,
    v.lastSeenAt = $now

MERGE (u)-[:USES_VAULT]->(v)

MERGE (u)-[lv:LOADED {loadId: $vaultLoadId}]->(v)
SET lv += $loadProvenance,
    lv.loadedAt = $now
""".strip()


# 4.3 step 1 — Documents.
WRITE_DOCUMENTS = """
UNWIND $documentRows AS row
MERGE (d:Document {uri: row.uri})
ON CREATE SET d += row.createOnly,
              d.firstLoadedAt = $now
SET d += row.props,
    d.lastLoadedAt = $now
WITH d
MATCH (v:Vault {uri: $vaultUri})
MERGE (v)-[:HAS_DOCUMENT]->(d)
""".strip()


# 4.3 step 2 — Sections.
WRITE_SECTIONS = """
UNWIND $sectionRows AS row
MERGE (s:Section {uri: row.uri})
ON CREATE SET s.firstLoadedAt = $now
SET s += row.props,
    s.lastLoadedAt = $now
""".strip()


# 4.3 step 3 — HAS_SECTION.
WRITE_HAS_SECTION = """
UNWIND $hasSectionRows AS row
MATCH (parent {uri: row.parentUri})
WHERE parent:Document OR parent:Section
MATCH (child:Section {uri: row.childUri})
MERGE (parent)-[:HAS_SECTION]->(child)
""".strip()


# 4.3 step 4a — clear stale NEXT_SECTION.
CLEAR_NEXT_SECTION = """
UNWIND $sectionRows AS row
MATCH (s:Section {uri: row.uri})-[r:NEXT_SECTION]-()
DELETE r
""".strip()


# 4.3 step 4b — build the NEXT_SECTION chain.
WRITE_NEXT_SECTION = """
UNWIND $nextSectionRows AS row
MATCH (src:Section {uri: row.srcUri})
MATCH (tgt:Section {uri: row.tgtUri})
MERGE (src)-[:NEXT_SECTION]->(tgt)
""".strip()


# 4.3 step 5 — per-doc LOADED provenance.
WRITE_DOC_LOADED = """
MATCH (u:User {id: $userId})
UNWIND $docLoadRows AS row
MATCH (d:Document {uri: row.docUri})
MERGE (u)-[ld:LOADED {loadId: $loadId}]->(d)
SET ld += $loadProps,
    ld.loadedAt = $now
""".strip()


# 4.3 step 6 — LINKS_TO.
WRITE_LINKS_TO = """
UNWIND $linksToRows AS row
MATCH (src {uri: row.srcUri})
WHERE src:Document OR src:Section
MATCH (tgt {uri: row.tgtUri})
WHERE tgt:Document OR tgt:Section
MERGE (src)-[l:LINKS_TO]->(tgt)
SET l.embed = row.embed,
    l.wikilink = row.wikilink
""".strip()


# 4.3 step 7 — Wikilink display-text → target aliases (Document or Section).
# Normalization happens client-side (see src/ki/parser/aliases.py); this
# query just unions the already-normalized batch into the target's aliases,
# preserving any pre-existing frontmatter aliases.
WRITE_DISPLAY_TEXT_ALIASES = """
UNWIND $aliasRows AS row
MATCH (n {uri: row.uri})
WHERE n:Document OR n:Section
WITH n, row.aliases AS newAliases,
     coalesce(n.aliases, []) AS existing
WITH n, existing,
     [a IN newAliases WHERE NONE (x IN existing WHERE toLower(x) = toLower(a))] AS toAdd
SET n.aliases = existing + toAdd
""".strip()


# --- Removal queries (used by `ki rm`). Not in ingest-cypher.md but follow the
# same single-uri MERGE-key model. `DETACH DELETE` removes incident
# relationships (HAS_SECTION, HAS_DOCUMENT, LOADED, LINKS_TO, NEXT_SECTION)
# along with their endpoints, per docs/requirements_v01_mvp.md *Removal*.
#
# Re-stitching NEXT_SECTION across removals is unnecessary for whole-doc
# deletion: NEXT_SECTION threads sections *within a single document* (see
# docs/data-model.md), so removing one doc's sections leaves other docs'
# chains untouched.
DELETE_DOCUMENT_AND_SECTIONS = """
MATCH (d:Document {uri: $docUri})
OPTIONAL MATCH (d)-[:HAS_SECTION*]->(s:Section)
WITH d, collect(DISTINCT s) AS secs
FOREACH (s IN secs | DETACH DELETE s)
DETACH DELETE d
""".strip()

# Delete an entire subtree of a vault by URI prefix (e.g. removing a folder).
# Uses STARTS WITH so we match all documents whose uri is `<vaultId>/<subpath>/...`.
DELETE_SUBTREE = """
MATCH (d:Document)
WHERE d.uri STARTS WITH $uriPrefix
OPTIONAL MATCH (d)-[:HAS_SECTION*]->(s:Section)
WITH collect(DISTINCT d) AS docs, collect(DISTINCT s) AS secs
FOREACH (s IN secs | DETACH DELETE s)
FOREACH (d IN docs | DETACH DELETE d)
""".strip()

COUNT_SUBTREE = """
MATCH (d:Document)
WHERE d.uri STARTS WITH $uriPrefix
RETURN count(d) AS doc_count
""".strip()

COUNT_VAULT = """
MATCH (v:Vault {uri: $vaultUri})
OPTIONAL MATCH (v)-[:HAS_DOCUMENT]->(d:Document)
OPTIONAL MATCH (d)-[:HAS_SECTION*]->(s:Section)
RETURN v.displayName AS display_name,
       count(DISTINCT d) AS doc_count,
       count(DISTINCT s) AS section_count
""".strip()

DELETE_VAULT = """
MATCH (v:Vault {uri: $vaultUri})
OPTIONAL MATCH (v)-[:HAS_DOCUMENT]->(d:Document)
OPTIONAL MATCH (d)-[:HAS_SECTION*]->(s:Section)
WITH v, collect(DISTINCT d) AS docs, collect(DISTINCT s) AS secs
FOREACH (s IN secs | DETACH DELETE s)
FOREACH (d IN docs | DETACH DELETE d)
DETACH DELETE v
""".strip()


# Lookup helpers.
GET_DOCUMENT_HASH = """
MATCH (d:Document {uri: $docUri})
RETURN d.fileHash AS fileHash
""".strip()

GET_VAULT_BY_URI = """
MATCH (v:Vault {uri: $vaultUri})
RETURN v.displayName AS displayName,
       v.path AS path,
       v.name AS name
""".strip()


# `ki vault list` — list every indexed vault with its user-authored description.
# Ordered most-recently-ingested first so the active vault floats to the top.
VAULT_LIST = """
MATCH (v:Vault)
RETURN v.uri AS uri,
       v.name AS name,
       v.displayName AS displayName,
       v.path AS path,
       v.description AS description,
       v.lastSeenAt AS lastSeenAt
ORDER BY v.lastSeenAt DESC, v.displayName
""".strip()
