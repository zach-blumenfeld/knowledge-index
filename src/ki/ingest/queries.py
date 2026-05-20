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

CONSTRAINT_FOLDER = """
CREATE CONSTRAINT folder_uri_unique IF NOT EXISTS
  FOR (f:Folder) REQUIRE f.uri IS UNIQUE
""".strip()

CONTENT_SEARCH_INDEX = """
CREATE FULLTEXT INDEX content_search IF NOT EXISTS
  FOR (n:Document|Section|Vault) ON EACH [n.displayName, n.content, n.aliases, n.description]
""".strip()

SCHEMA_STATEMENTS = (
    CONSTRAINT_USER,
    CONSTRAINT_VAULT,
    CONSTRAINT_FOLDER,
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


# 4.3 step 1 — Documents (node upsert only; parent HAS edge lives in step 1c).
WRITE_DOCUMENTS = """
UNWIND $documentRows AS row
MERGE (d:Document {uri: row.uri})
ON CREATE SET d += row.createOnly,
              d.firstLoadedAt = $now
SET d += row.props,
    d.lastLoadedAt = $now
""".strip()


# 4.3 step 1b — Folders (node upsert only).
# $folderRows: list of { uri, props } where
#   props = { name, displayName, path }
# `path` updates on every ingest (machine-scoped, last-write-wins) so the
# always-run `SET f += row.props` is load-bearing — not folded into ON CREATE.
WRITE_FOLDERS = """
UNWIND $folderRows AS row
MERGE (f:Folder {uri: row.uri})
ON CREATE SET f.firstSeenAt = $now
SET f += row.props,
    f.lastSeenAt = $now
""".strip()


# 4.3 step 1c — folder/document tree HAS edges.
# Single edge type for all containment in the vault/folder/document layer.
# Covers all four valid endpoint shapes in a single UNWIND:
#   Vault   -[:HAS]-> Folder      (top-level folder)
#   Vault   -[:HAS]-> Document    (root-level document, no enclosing folder)
#   Folder  -[:HAS]-> Folder      (nested subdirectory)
#   Folder  -[:HAS]-> Document    (document under that folder)
# Each child gets exactly one incoming HAS edge — the writer picks the
# immediate parent (Vault for root-level children, the containing Folder
# otherwise) and emits one row per child.
# $treeEdgeRows: list of { parentUri, childUri }
WRITE_TREE_EDGES = """
UNWIND $treeEdgeRows AS row
MATCH (parent {uri: row.parentUri})
WHERE parent:Vault OR parent:Folder
MATCH (child {uri: row.childUri})
WHERE child:Folder OR child:Document
MERGE (parent)-[:HAS]->(child)
""".strip()


# 4.3 step 2 — Sections.
WRITE_SECTIONS = """
UNWIND $sectionRows AS row
MERGE (s:Section {uri: row.uri})
ON CREATE SET s.firstLoadedAt = $now
SET s += row.props,
    s.lastLoadedAt = $now
""".strip()


# Path-only refresh for documents skipped via fileHash match.
#
# When a document's bytes are unchanged across ingests, the writer skips the
# normal WRITE_DOCUMENTS / WRITE_SECTIONS pass — but `Document.path` and
# `Section.path` are machine-scoped and may have shifted (vault moved from
# one mount to another). This pass stamps the new path on the skipped
# document and propagates to every Section under it.
#
# $pathRefreshRows: list of { docUri, path }
REFRESH_DOC_AND_SECTION_PATHS = """
UNWIND $pathRefreshRows AS row
MATCH (d:Document {uri: row.docUri})
SET d.path = row.path
WITH d, row
OPTIONAL MATCH (d)-[:HAS*]->(s:Section)
SET s.path = row.path
""".strip()


# 4.3 step 3 — section-tree HAS edges (Document|Section → Section).
# Same `:HAS` relationship type as the vault/folder/document tree (step 1c
# in docs/ingest-cypher.md); kept in its own step here because section
# trees are constructed per-document alongside section node writes, while
# folder trees are constructed per-vault.
WRITE_SECTION_EDGES = """
UNWIND $hasSectionRows AS row
MATCH (parent {uri: row.parentUri})
WHERE parent:Document OR parent:Section
MATCH (child:Section {uri: row.childUri})
MERGE (parent)-[:HAS]->(child)
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


# --- Vault-level removal queries. See `docs/index_rm_behavior.md` for the
# full design (vault-level sync model, three-step removal routine, batched
# DETACH DELETE rationale).
#
# Step counts and the "remove" vocabulary throughout match the behavior doc.

# Pre-removal count, surfaced in confirmation prompts.
COUNT_VAULT = """
MATCH (v:Vault {uri: $vaultUri})
OPTIONAL MATCH (v)-[:HAS*]->(d:Document)
OPTIONAL MATCH (d)-[:HAS*]->(s:Section)
RETURN v.displayName AS display_name,
       count(DISTINCT d) AS doc_count,
       count(DISTINCT s) AS section_count
""".strip()


# Step 1 — snapshot the URIs of LINKS_TO targets that sit OUTSIDE the vault
# being removed. These are the only external nodes whose degree could
# plausibly drop to zero as a result of the upcoming removal, so they're
# the only ones step 3 needs to recheck.
#
# The URI-prefix-match (`STARTS WITH $vaultUri + '/'`) leverages the slug
# being a strict prefix of every Folder/Document/Section URI under the vault.
COLLECT_EXTERNAL_LINKS_TARGETS = """
MATCH (src)-[:LINKS_TO]->(tgt)
WHERE (src.uri = $vaultUri OR src.uri STARTS WITH $vaultUri + '/')
  AND NOT (tgt.uri = $vaultUri OR tgt.uri STARTS WITH $vaultUri + '/')
RETURN DISTINCT tgt.uri AS uri
""".strip()


# Step 2 — batched DETACH DELETE of the vault subtree. The `$chunkSize`
# placeholder is substituted client-side because `CALL ... IN TRANSACTIONS
# OF n ROWS` rejects Cypher parameters in the `n` position (same trick as
# B.3 / B.12 quantified-path quantifiers). See `run_remove_vault_subtree`.
#
# Note: this MUST be run from an *implicit* transaction (driver-level
# `session.run`), not inside `session.execute_write` — `CALL IN TRANSACTIONS`
# explicitly forbids nesting in a managed transaction.
REMOVE_VAULT_SUBTREE_BATCHED = """
MATCH (n) WHERE n.uri = $vaultUri OR n.uri STARTS WITH $vaultUri + '/'
CALL (n) {
  DETACH DELETE n
} IN TRANSACTIONS OF $chunkSize ROWS
""".strip()


# Step 3 — orphan GC scoped to the snapshot from step 1. Same chunk-size
# substitution rule. The `WHERE NOT (n)--()` clause is the degree-zero
# check (no incident edges remain after step 2 finished).
REMOVE_ORPHAN_TARGETS_BATCHED = """
UNWIND $candidateUris AS u
MATCH (n {uri: u})
WHERE NOT (n)--()
CALL (n) {
  DETACH DELETE n
} IN TRANSACTIONS OF $chunkSize ROWS
""".strip()


# `ki nuke` — enumerate every vault's URI and machine-scoped path so the
# caller can clean `.ki/vault.yaml` markers from disk after the graph wipe.
LIST_ALL_VAULTS = """
MATCH (v:Vault)
RETURN v.uri AS uri, v.path AS path
ORDER BY v.uri
""".strip()


# `ki nuke` — batched DETACH DELETE of every node in the graph. Same
# chunk-size substitution rule. Run before dropping schema.
REMOVE_ALL_NODES_BATCHED = """
MATCH (n)
CALL (n) {
  DETACH DELETE n
} IN TRANSACTIONS OF $chunkSize ROWS
""".strip()


# `ki nuke` — drop ki-owned constraints and indexes. Each is `IF EXISTS`-
# guarded so the call is idempotent (an already-nuked graph runs cleanly).
# Names match `SCHEMA_STATEMENTS` above; keep these two lists in lock-step.
DROP_SCHEMA_STATEMENTS = (
    "DROP CONSTRAINT user_id_unique IF EXISTS",
    "DROP CONSTRAINT vault_uri_unique IF EXISTS",
    "DROP CONSTRAINT folder_uri_unique IF EXISTS",
    "DROP CONSTRAINT document_uri_unique IF EXISTS",
    "DROP CONSTRAINT section_uri_unique IF EXISTS",
    "DROP INDEX content_search IF EXISTS",
)


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
