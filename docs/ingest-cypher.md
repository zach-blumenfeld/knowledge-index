### 4.3 MERGE key strategy

All `Vault`, `Document`, and `Section` nodes MERGE on `uri`:

- `Vault.uri`    = UUID v4 from the `.ki/vault-id` marker file in the vault root.
- `Document.uri` = `<vaultId>/<file path within vault>` (slugified).
- `Section.uri`  = `<vaultId>/<file path within vault>#<slugified heading path>`.

`User` nodes MERGE on the system-provided `id`.

`LOADED` relationships can be parallel (one per ingest) and so require a relationship-level MERGE key: a system-generated UUID stored as `loadId`. All other relationships (`USES_VAULT`, `HAS_DOCUMENT`, `HAS_SECTION`, `LINKS_TO`) are non-parallel and MERGE on the endpoint pair alone.

**Vault marker file.** On first ingest of a folder, the writer reads `.ki/vault-id`. If present, that UUID is the vault identity. If absent, a fresh UUID is generated and written into the marker. Treating the marker as authoritative means a folder synced across machines (Dropbox, iCloud, git) resolves to the same `:Vault` node across users and machines, and `USES_VAULT` becomes load-bearing (multiple users can `USES_VAULT` the same vault). Identity is independent of user and machine; only `Vault.path` is machine-scoped.

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
SET v += $vaultMutable,       // name, displayName, path, isObsidianVault
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

**Write order matters:** documents and sections first (so their `uri`s exist as MATCH targets), then `HAS_SECTION` (tree edges), then `NEXT_SECTION` (linear reading-order chain — cleared and rebuilt each ingest), then User→Document `LOADED` provenance edges, then `LINKS_TO` (so cross-document wikilink targets resolve), and finally the wikilink-display-text → target `aliases` aggregation (step 7), which depends on the vault-wide set of `LINKS_TO` display texts already being gathered client-side.

```cypher
// 1. Documents — batched upsert.
// $documentRows: list of { uri, createOnly, props } where
//   createOnly = { frontmatterCreatedAt }   // first-write-wins fields
//   props      = { name, displayName, aliases, fileHash, frontmatter,
//                  content, sourceType }
UNWIND $documentRows AS row
MERGE (d:Document {uri: row.uri})
ON CREATE SET d += row.createOnly,
              d.firstLoadedAt = $now
SET d += row.props,
    d.lastLoadedAt = $now
WITH d
MATCH (v:Vault {uri: $vaultUri})
MERGE (v)-[:HAS_DOCUMENT]->(d)
```

```cypher
// 2. Sections — batched upsert.
// $sectionRows: list of { uri, props } where
//   props = { name, displayName, headingLevel, content }
UNWIND $sectionRows AS row
MERGE (s:Section {uri: row.uri})
ON CREATE SET s.firstLoadedAt = $now
SET s += row.props,
    s.lastLoadedAt = $now
```

```cypher
// 3. HAS_SECTION — batched edges from (Document|Section) parent to Section child.
// $hasSectionRows: list of { parentUri, childUri }
// Parent label is resolved by URI; both Document and Section URIs live in the
// same uniqueness namespace, so a plain MATCH on {uri} is sufficient.
UNWIND $hasSectionRows AS row
MATCH (parent {uri: row.parentUri})
WHERE parent:Document OR parent:Section
MATCH (child:Section {uri: row.childUri})
MERGE (parent)-[:HAS_SECTION]->(child)
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
// both `Document` and `Section` (see the `doc_section_search` index in
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

// LOADED uniqueness is handled by MERGE semantics on the `loadId` property
// between two already-unique endpoints — no explicit relationship-key constraint
// is required (and relationship-key constraints are Enterprise-only / unavailable
// on Aura Free).

// Fulltext is the *primary* retrieval substrate in v1 (no embeddings).
// Index `displayName` (human-readable heading / filename), `content`, and
// `aliases` (list-valued — frontmatter alternate names plus piped-wikilink
// display texts, so queries like "JFK" / "John F Kennedy" or "Anakin" hit
// the right target). Both `Document` and `Section` carry `aliases` as of
// v0.3.0 — Neo4j fulltext silently skips a missing property, so a single
// combined index covers both labels.
// The mapper writes `content` with `uri:` child-pointer lines appended, which
// add some junk tokens to the index; if recall suffers, switch to a sanitised
// `contentForIndex` copy that strips those pointer lines.
CREATE FULLTEXT INDEX doc_section_search IF NOT EXISTS
  FOR (n:Document|Section) ON EACH [n.displayName, n.content, n.aliases];

// (Vector indexes intentionally omitted — embeddings are deferred. See Q4.)
```

> **Note on the old `tag_search` index.** The previous draft created a fulltext index over a `:Tag` node label. Tags are no longer a first-class entity in the target data model, so the `tag_unique` constraint and `tag_search` fulltext index have been dropped. If tag retrieval comes back, reintroduce both at that point.
