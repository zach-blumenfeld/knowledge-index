### 4.3 MERGE key strategy

All `Vault`, `Folder`, `Document`, and `Section` nodes MERGE on `uri`:

- `Vault.uri`    = human-readable slug from the `.ki/vault.yaml` marker file in the vault root (slugified directory basename, with `-N` suffix on collision).
- `Folder.uri`   = `<vaultId>/<slugified directory path within vault>` (no trailing `/`).
- `Document.uri` = `<vaultId>/<file path within vault>` (slugified).
- `Section.uri`  = `<vaultId>/<file path within vault>#<slugified heading path>`.

The `Folder` URI scheme is a strict *prefix* of any `Document` URI living under it (after slugification of each path segment), which is what makes `STARTS WITH`-based subtree queries cheap.

`User` nodes MERGE on the system-provided `id`.

`LOADED` relationships can be parallel (one per ingest) and so require a relationship-level MERGE key: a system-generated UUID stored as `loadId`. All other relationships (`USES_VAULT`, `HAS`, `LINKS_TO`) are non-parallel and MERGE on the endpoint pair alone.

**Vault marker file.** On first ingest of a folder, the writer reads `.ki/vault.yaml`. If present, its `uri:` field is the vault identity. If absent, the writer computes a base slug from the directory basename (`compute_base_slug` in `src/ki/vault.py`), queries the graph for any `Vault` nodes already in the `{base, base-1, base-2, ...}` family, picks the next unclaimed slug (max+1 over currently-present slugs — deleted slugs become available for reassignment), `CREATE`s the `:Vault` node under a uniqueness constraint to catch parallel writers, and writes the assigned slug back to `.ki/vault.yaml`. Treating the marker as authoritative on subsequent ingests means a folder synced across machines (Dropbox, iCloud, git) resolves to the same `:Vault` node across users and machines, and `USES_VAULT` becomes load-bearing (multiple users can `USES_VAULT` the same vault). Identity is independent of user and machine once assigned; only `Vault.path` is machine-scoped.

The same file also optionally carries a user-authored `description:` field — a short routing hint about what this vault is for. On each ingest the writer reads it and includes it in `$vaultMutable` (see step 2 of the per-vault-ingest write below), so it flows into `Vault.description` with latest-write-wins semantics. `ki` writes `uri:` on first creation (and on subsequent ingests, idempotently re-stamping the same slug) and is **read-only** w.r.t. every other field except `description` when the user passes `--description`.

#### Per-vault-ingest write

Run once per `(user, vault)` per ingest, before any document writes. Establishes the user, the vault, the `USES_VAULT` edge, and records a single vault-level `LOADED` provenance edge for this ingest run.

```cypher
// 1. Upsert the User.
MERGE (u:User {id: $userId})
ON CREATE SET u.firstSeenAt = $now
SET u += $userMutable,        // displayName, email
    u.lastSeenAt = $now

// 2. Upsert the Vault.
MERGE (v:Vault {uri: $vaultUri})
ON CREATE SET v.firstSeenAt = $now
SET v += $vaultMutable,       // name, displayName, path, isObsidianVault, description (optional)
    v.lastSeenAt = $now

// 3. Membership edge: User USES_VAULT Vault (non-parallel).
MERGE (u)-[:USES_VAULT]->(v)

// 4. Provenance edge: User LOADED Vault (parallel — keyed by loadId).
MERGE (u)-[lv:LOADED {loadId: $vaultLoadId}]->(v)
SET lv += $loadProvenance,    // agentName, agentVersion, modelId, graphVaultVersion,
                              // timezone, locale, os, osVersion, hostname, pythonVersion
    lv.loadedAt = $now
```

#### Batched per-vault writes

Run after the per-vault-ingest write. All document/section/relationship writes for a single vault are batched via `UNWIND $rows AS row` — driver-side, the writer accumulates rows in chunks (e.g. 1–5k rows per call) and ships each chunk in a single transaction. Single-row MERGEs are ~10–100× slower than batched UNWIND against Neo4j, so this is the path used in production.

Each `row` is a plain dict. `row.props` is the mutable property bag fed into `SET n += row.props`; create-only fields are split out into `row.createOnly` so they're only applied on `ON CREATE`.

**Write order matters:** documents, folders, and sections first (so their `uri`s exist as MATCH targets), then folder/doc tree `HAS` edges (step 1c), then section tree `HAS` edges (step 3), then `NEXT_SECTION` (linear reading-order chain — cleared and rebuilt each ingest), then User→Document `LOADED` provenance edges, then **stub + external Documents** (step 5.5 / 5.6 — new in #37, batch their folder materialization + MERGE before LINKS_TO so the link-targets exist as MATCH targets), then `LINKS_TO`, and finally the link-display-text → target `aliases` aggregation (step 7), which depends on the vault-wide set of `LINKS_TO` display texts already being gathered client-side.

```cypher
// 1. Documents — batched node upsert (no parent edge yet — see step 1c).
// $documentRows: list of { uri, createOnly, props } where
//   createOnly = { frontmatterCreatedAt }   // first-write-wins fields
//   props      = { name, displayName, path, aliases, fileHash, frontmatter,
//                  content, sourceType }
//
// `path` is the absolute POSIX file path on the ingesting machine. Same
// machine-scoped caveat as `Vault.path` (see data-model.md §Vault). Stamped
// on every ingest; last writer wins if the same vault is indexed from two
// different mount points.
UNWIND $documentRows AS row
MERGE (d:Document {uri: row.uri})
ON CREATE SET d += row.createOnly,
              d.firstLoadedAt = $now
SET d += row.props,
    d.lastLoadedAt = $now
```

```cypher
// 1b. Folders — batched node upsert (nodes only, no edges yet).
//
// The writer computes the set of distinct directory paths across all indexed
// documents and ships one row per Folder. Folder MERGE is on `uri` (= the
// slugified directory path under `$vaultId`); nodes are created on first
// sight and touched on every ingest.
//
// `path` is the absolute POSIX directory path on the ingesting machine.
// Same machine-scoped caveat as `Vault.path` and `Document.path`.
//
// $folderRows: list of { uri, props } where
//   props = { name, displayName, path }
//
// `path` is the load-bearing case for the always-run `SET f += row.props`:
// if the vault is re-indexed from a different mount point, the existing
// Folder's `path` updates to the new absolute path. Same last-write-wins
// rule as `Vault.path`.
UNWIND $folderRows AS row
MERGE (f:Folder {uri: row.uri})
ON CREATE SET f.firstSeenAt = $now
SET f += row.props,
    f.lastSeenAt = $now
```

```cypher
// 1c. HAS — batched edges for the vault/folder/document tree.
//
// Single edge type for all containment (see `docs/data-model.md` §4.2 *Why
// one relationship type instead of three*). One UNWIND covers every parent
// → child relationship in the folder/doc layer:
//
//    Vault   -[:HAS]-> Folder      (top-level folder)
//    Vault   -[:HAS]-> Document    (root-level document, no enclosing folder)
//    Folder  -[:HAS]-> Folder      (nested subdirectory)
//    Folder  -[:HAS]-> Document    (document under that folder)
//
// Each child gets exactly one incoming HAS edge — the writer picks the
// immediate parent (Vault for root-level children, the containing Folder
// otherwise) and emits one row per child. Parent and child labels are
// resolved by URI lookup; the WHERE filter on labels keeps the query
// honest about what shapes are legal.
//
// $treeEdgeRows: list of { parentUri, childUri }
UNWIND $treeEdgeRows AS row
MATCH (parent {uri: row.parentUri})
WHERE parent:Vault OR parent:Folder
MATCH (child {uri: row.childUri})
WHERE child:Folder OR child:Document
MERGE (parent)-[:HAS]->(child)
```

```cypher
// 2. Sections — batched node upsert (no parent edge yet — see step 3).
// $sectionRows: list of { uri, props } where
//   props = { name, displayName, headingLevel, content, path }
//
// `path` is the absolute POSIX path of the owning Document — redundant with
// `Document.path` but stamped on every Section for one-shot agent UX (any
// Section query result is enough to `Read` the file). Last writer wins on
// re-ingest from a different mount, mirroring Document.path / Folder.path.
UNWIND $sectionRows AS row
MERGE (s:Section {uri: row.uri})
ON CREATE SET s.firstLoadedAt = $now
SET s += row.props,
    s.lastLoadedAt = $now
```

```cypher
// 3. HAS — batched edges for the section tree (Document|Section → Section).
//
// Same relationship type as step 1c — `HAS` is the universal containment
// edge across the whole hierarchy. Kept in its own step (separate from 1c)
// because section trees are *per-document* and constructed alongside section
// node writes; folder trees are *per-vault* and constructed once.
//
// $sectionEdgeRows: list of { parentUri, childUri }
UNWIND $sectionEdgeRows AS row
MATCH (parent {uri: row.parentUri})
WHERE parent:Document OR parent:Section
MATCH (child:Section {uri: row.childUri})
MERGE (parent)-[:HAS]->(child)
```

```cypher
// 4a. NEXT_SECTION — clear stale chain.
//
// NEXT_SECTION threads every section of a document in DFS reading order. On
// re-ingest, sections may have been added, removed, or reordered, so the
// existing chain can be wrong. Cheapest correct approach: blow it away and
// rebuild from the new ordered list (4b).
//
// We delete any NEXT_SECTION edge incident to a section being (re-)ingested.
// Reusing $sectionRows means the writer doesn't need a separate parameter.
UNWIND $sectionRows AS row
MATCH (s:Section {uri: row.uri})-[r:NEXT_SECTION]-()
DELETE r
```

```cypher
// 4b. NEXT_SECTION — batched chain construction.
//
// The writer computes the DFS reading order client-side (it already parses
// the document tree) and ships consecutive (src, tgt) pairs as $nextSectionRows.
// One row per edge — for a document with N sections, that's N-1 rows.
//
// $nextSectionRows: list of { srcUri, tgtUri }
UNWIND $nextSectionRows AS row
MATCH (src:Section {uri: row.srcUri})
MATCH (tgt:Section {uri: row.tgtUri})
MERGE (src)-[:NEXT_SECTION]->(tgt)
```

```cypher
// 5. User LOADED Document — batched provenance edges (parallel, keyed by loadId).
//
// Load-level provenance is identical for every document in a single ingest, so
// it is passed once as $loadProps (lifted out of the UNWIND) instead of being
// duplicated across N rows. Per-row data is just the document URI.
//
// $loadId:      UUID for this ingest event. Typically the SAME value used for
//               the User→Vault LOADED edge in the per-vault-ingest write, so
//               all edges produced by one ingest share a loadId and can be
//               retrieved together.
// $loadProps:   { agentName, agentVersion, modelId, graphVaultVersion,
//                 timezone, locale, os, osVersion, hostname, pythonVersion }
// $docLoadRows: list of { docUri }
MATCH (u:User {id: $userId})
UNWIND $docLoadRows AS row
MATCH (d:Document {uri: row.docUri})
MERGE (u)-[ld:LOADED {loadId: $loadId}]->(d)
SET ld += $loadProps,
    ld.loadedAt = $now
```

```cypher
// 6. LINKS_TO — batched edges between Documents/Sections.
// $linksToRows: list of { srcUri, tgtUri, embed, wikilink }
// Run last so cross-document wikilink targets exist.
UNWIND $linksToRows AS row
MATCH (src {uri: row.srcUri})
WHERE src:Document OR src:Section
MATCH (tgt {uri: row.tgtUri})
WHERE tgt:Document OR tgt:Section
MERGE (src)-[l:LINKS_TO]->(tgt)
SET l.embed = row.embed,
    l.wikilink = row.wikilink
```

```cypher
// 7. Wikilink display-text → target aliases.
//
// When the parser sees `[[Target|Display]]` or `[[Target#Section|Display]]`,
// the display text is the alternate name *the user* gave the target in
// running prose. Today, fulltext search against `aliases` already covers
// both `Document` and `Section` (see the `content_search` index in
// §4.4), but the alias list itself is only populated from frontmatter —
// so a vault that pipes `[[Darth Vader|Anakin]]` everywhere never matches
// the literal query "Anakin". This step closes the gap: aggregate display
// texts client-side per target URI, normalize, and union them into the
// target's `aliases` field.
//
// Normalization happens client-side (trim, length >= 3, stopword filter,
// drop if equal to the target's displayName, lowercase-dedup within the
// new batch, per-target cap at 50, sorted by occurrence count desc then
// alphabetically). Frontmatter aliases are the user's ground truth and
// must not be displaced — we UNION (via apoc.coll.toSet-equivalent) rather
// than overwrite.
//
// $aliasRows: list of { uri, aliases } where `aliases` is the already-
//             normalized list of new display-text aliases for this target.
// Targets that have section endpoints update `Section.aliases`; targets
// that have document endpoints update `Document.aliases`. A single MATCH
// keyed on `uri` covers both labels because Document and Section URIs
// share the same uniqueness namespace.
UNWIND $aliasRows AS row
MATCH (n {uri: row.uri})
WHERE n:Document OR n:Section
WITH n, row.aliases AS newAliases,
     coalesce(n.aliases, []) AS existing
WITH n, existing,
     [a IN newAliases WHERE NONE (x IN existing WHERE toLower(x) = toLower(a))] AS toAdd
SET n.aliases = existing + toAdd
```

### 4.4 Schema / constraints

Emit once at `graph-vault init` time:

```cypher
// Node uniqueness — single-property `uri` is the MERGE key for Vault/Document/Section.
CREATE CONSTRAINT user_id_unique IF NOT EXISTS
  FOR (u:User) REQUIRE u.id IS UNIQUE;

CREATE CONSTRAINT vault_uri_unique IF NOT EXISTS
  FOR (v:Vault) REQUIRE v.uri IS UNIQUE;

CREATE CONSTRAINT document_uri_unique IF NOT EXISTS
  FOR (d:Document) REQUIRE d.uri IS UNIQUE;

CREATE CONSTRAINT section_uri_unique IF NOT EXISTS
  FOR (s:Section) REQUIRE s.uri IS UNIQUE;

CREATE CONSTRAINT folder_uri_unique IF NOT EXISTS
  FOR (f:Folder) REQUIRE f.uri IS UNIQUE;

// LOADED uniqueness is handled by MERGE semantics on the `loadId` property
// between two already-unique endpoints — no explicit relationship-key constraint
// is required (and relationship-key constraints are Enterprise-only / unavailable
// on Aura Free).

// Fulltext is the *primary* retrieval substrate in v1 (no embeddings).
// Index `displayName` (human-readable heading / filename), `content`,
// `aliases` (list-valued — frontmatter alternate names plus piped-wikilink
// display texts, so queries like "JFK" / "John F Kennedy" or "Anakin" hit
// the right target), and `description` (user-authored vault routing hint,
// only present on `:Vault`). As of v0.4.0 the same index covers all three
// searchable node labels — Neo4j fulltext silently skips a missing property
// per label, so `:Document` / `:Section` rows simply have no `description`
// and `:Vault` rows have no `content` / `aliases`. `ki search` filters by
// label in the query (B.1 → `:Document`, B.2 → `:Section`, B.11 → `:Vault`).
//
// `:Folder` is deliberately **not** included — folders carry no `content`,
// `aliases`, or `description` (see `docs/data-model.md` §Folder). They're a
// navigation surface, not a retrieval surface. `ki tree` and `--under`
// scoping use graph traversal (HAS edges), not fulltext.
// The mapper writes `content` with `uri:` child-pointer lines appended, which
// add some junk tokens to the index; if recall suffers, switch to a sanitised
// `contentForIndex` copy that strips those pointer lines.
CREATE FULLTEXT INDEX content_search IF NOT EXISTS
  FOR (n:Document|Section|Vault) ON EACH [n.displayName, n.content, n.aliases, n.description];

// (Vector indexes intentionally omitted — embeddings are deferred. See Q4.)
```

> **Note on the old `tag_search` index.** The previous draft created a fulltext index over a `:Tag` node label. Tags are no longer a first-class entity in the target data model, so the `tag_unique` constraint and `tag_search` fulltext index have been dropped. If tag retrieval comes back, reintroduce both at that point.
