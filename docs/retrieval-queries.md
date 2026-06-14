## Retrieval queries for the new data model

Same query *shapes* as [docs/research](research-data-model/research-retrieval-queries.md), ported to the new
`(User)–(Vault)–(Folder)–(Document)–(Section)` schema.

### Mapping cheatsheet
| Old (Wikipedia)                            | New (Graph Vault)                                                                  |
|--------------------------------------------|------------------------------------------------------------------------------------|
| `:Article (title, embedding)`              | `:Document (uri, displayName, content, frontmatter, aliases)`                      |
| `:Section`                                 | `:Section (uri, displayName, headingLevel, content)`                               |
| `:Paragraph (text)`                        | — folded into `Section.content`                                                    |
| `:Chunk (text, embedding)`                 | — deferred (v1 has no embeddings)                                                  |
| `:HAS_SECTION` / `:HAS_PARAGRAPH` (linear) | `:HAS` (universal containment edge — `Vault\|Folder\|Document\|Section` → `Folder\|Document\|Section`; see `docs/data-model.md` §4.2) |
| `:NEXT_SECTION` / `:NEXT_PARAGRAPH`        | `:NEXT_SECTION` — threads ALL sections of a document in DFS reading order          |
| `:MENTIONS` (article-to-article projected) | `:LINKS_TO` (`Document`\|`Section` → `Document`\|`Section`)                        |
| `:REDIRECTS_TO`                            | `Document.aliases` (wikilinks resolved at ingest time)                             |
| Vector indexes on title / chunk            | Fulltext index `content_search` on `displayName` + `content` + `aliases` + `description` (covers `:Document`, `:Section`, **and** `:Vault`) |

> **`NEXT_SECTION` semantics.** The new model threads every section of a
> document into a single linear chain in DFS reading order — top to bottom as
> a human would read the file. The chain crosses heading levels: the last
> descendant of an `H1` points to the next `H1`, not to a sibling at the same
> level. This makes "read the whole doc" and "give me ±N peers" cheap walks
> rather than tree gymnastics. See `target-data-model-cypher.md` §4.3 step 4.

### Parameters
- `$uri` — the `Document.uri` (slugified `<vault-slug>/<path>`, see §4.3).
- `$section_uri`, `$section_uris` — same convention for sections.
- `$folder_uri` — a `Folder.uri` (slugified `<vault-slug>/<dir-path>`) used as a prefix in `--under` scoping for B.1/B.2/B.11 — see *Scoping* below.
- `$root_uri` — for B.12 / `ki outline`; URI of any node to start the tree walk from (`:Vault`, `:Folder`, `:Document`, or `:Section`). When `null` / absent, B.12 falls back to matching every `:Vault` in the graph — see B.12 below.
- `$source_uris` — for B.12-links / `ki outline`; list of `:Document` and `:Section` URIs to fetch outbound `:LINKS_TO` edges from. Populated by the renderer from the B.12 result.
- `$index_name` — `'content_search'` (the fulltext index from §4.4).
- `$query` — fulltext query string (Lucene syntax).
- `$k`, `$n` — limit / window size.
- `$depth` — for B.12 / `ki outline`; cap on tree traversal depth (must be >= 1).

### Scoping with `:Folder` (`--under`)

`Folder.uri` is a strict path prefix of every `Document.uri` (and therefore every `Section.uri`) under it, so scoping any fulltext query to a folder subtree is a cheap `STARTS WITH` filter post-fulltext:

```cypher
CALL db.index.fulltext.queryNodes($index_name, $query)
YIELD node, score
WHERE node:Document AND node.uri STARTS WITH $folder_uri + '/'
...
```

The same idea works for any of B.1 / B.2 / B.11 (with `:Vault`-scope `folder_uri` = the whole vault URI, which is itself a prefix of every node URI in it). No graph traversal needed for scoping — `STARTS WITH` against the slugified URI prefix is enough. CLI flag mapping: `ki search "..." --under <folder-uri>`.

### Per-query design notes
- **B.1 / B.2 / B.11 (vector → fulltext)** — v1 has no vector index per §4.4, so all three use `content_search` (the unified `Document|Section|Vault` fulltext index, over `displayName` + `content` + `aliases` + `description`). Filter by label (`:Document` / `:Section` / `:Vault`) post-yield. Notes on swapping to vector when added.
- **B.3 neighbourhood** — `:MENTIONS` → `:LINKS_TO`; alias-based redirect resolution happens at ingest, so no `REDIRECTS_TO*` canonicalisation needed. Traversal flows through `(doc)-[:HAS*0..]->(elem)` to handle section-as-link-endpoint, then projects each hop back to its owning document.
- **B.4 document text** — walks `NEXT_SECTION*0..` from the first section (the direct `HAS` child with no incoming `NEXT_SECTION`), orders by path length, adds `reading_order` to the output. Defensive `(start)-[:HAS*]->(section)` keeps the walk inside this document. Matches the shape of old A.4 closely.
- **B.5 frontmatter** — `infobox` → `frontmatter` (the natural structured-metadata analog); same row shape (`kind`, `uri`, `text`). Sections returned in strict DFS reading order via the `NEXT_SECTION` walk.
- **B.6 get sections** — straight URI lookup; sections own their content directly (no paragraph hop).
- **B.7 windowing (full content)** — near-verbatim port of old A.7's paragraph windowing. Symmetric `back_path` / `fwd_path` collect-and-merge over `NEXT_SECTION` with `offset`, full content. Old A.7 + A.8 collapse here since the new model has no `Paragraph`.
- **B.8 windowing (summary)** — same `±N` walk as B.7 over `NEXT_SECTION` but returns `heading`, `first_child_section_uri`, `child_count` instead of full content. The `first_child` lookup exploits the DFS property: a section's first child is the section it points to via `NEXT_SECTION` that is also a `HAS` child of it. Mirrors A.8's summary semantics.
- **B.9 / B.10** — straightforward `LINKS_TO` ports with endpoint-projection (any section endpoint → its owning `Document`) so the result shape stays at document granularity like the originals.
- **B.11 Vault fulltext** — see *Per-query* notes above; same shared `content_search` index, filtered to `:Vault`. Powers `ki search --type vault` for cross-vault routing.
- **B.12 / B.12-links Containment tree** — `ki outline`'s engine. Two queries:
  - **B.12** walks `:HAS` from `$root_uri` up to `$depth` steps, depth-capped via a quantified path pattern (`{1,$depth}`) so the bound prunes during traversal rather than as a post-filter (same trick as B.3). Returns one row per node — root + HAS descendants — in the wire format defined by `docs/outline-format.md` *Wire record format*: `{depth, inrel, label, name, displayName, uri, parent_uri, sort_pos}`. Sections carry `sort_pos` (NEXT_SECTION position in their parent document) so the renderer can order sibling sections by reading order, not alphabetically. When `$root_uri` is `null` / absent, B.12 matches every `:Vault` as a root and fans out — the renderer treats them as a multi-root sibling group at `parent_uri = null`, sorted alphabetically by `name`. Multi-user scoping via `:USES_VAULT` is a follow-up; single-user makes "all vaults" unambiguous today.
  - **B.12-links** is the LINKS_TO sub-pass: given a list of D/S URIs (`$source_uris`), returns their outbound `:LINKS_TO` edges. The renderer combines B.12 + B.12-links output, groups by `parent_uri`, sorts per the rules in `docs/outline-format.md` *Sibling ordering*, and DFS-emits.
  - Splitting hierarchy and LINKS_TO into two queries keeps each small. The cost is one extra round trip, paid every `ki outline` invocation.
- **B.13 / B.14 `ki get`** — fetch a node's metadata and content by URI. Two queries:
  - **B.13** is a single-node lookup that returns the union of properties across `:Document` and `:Section` (Neo4j returns `null` for missing properties, so one query covers both). The dispatcher in `src/ki/commands/get.py` keys off `label` to pick the relevant subset for output. `ki get` rejects `:Folder` and `:Vault` URIs with a hint pointing at `ki outline` / `ki vault list` — text retrieval isn't what those nodes represent. B.13 is the metadata "shell" returned for every `ki get` row regardless of `--type`.
  - **B.14** is the section-subtree reconstruction used only when `--type full` is applied to a `:Section` URI. Walks the same `NEXT_SECTION` chain B.4 walks, but bounded to the subtree under the start section via `start = s OR (start)-[:HAS*]->(s)`. For `--type full` on a `:Document` URI the dispatcher calls **B.4** directly (the existing document-text query) plus a separate read of `Document.content` for the preamble.
  - Lift convention: B.4 / B.13 / B.14 are all lifted into `src/ki/search/queries.py` as constants per the AGENTS.md rule that Cypher source-of-truth lives under `docs/`.


B.1 Document title fulltext search
```cypher
// Vector replacement: `content_search` indexes displayName + content +
// aliases. To bias toward titles, boost in the Lucene query
// (e.g. `displayName:"foo"^3 foo`) or post-filter by displayName.
// When a vector index is added (deferred — see §4.4), swap to `db.index.vector.queryNodes`.
CALL db.index.fulltext.queryNodes($index_name, $query)
YIELD node, score
WHERE node:Document
RETURN node.uri AS document_uri,
       node.displayName AS title,
       node.path AS path,
       score
ORDER BY score DESC
LIMIT toInteger($k)
```

B.2 Section content fulltext search
```cypher
// Section is the smallest content unit in the new model (no Paragraph / Chunk).
// Roll each section hit up to its owning document so callers get both
// granularities — the section for retrieval, the document for context.
//
// `path` is the absolute POSIX file path of the owning Document on the
// ingesting machine — same value on both `doc.path` and `section.path` by
// construction. We surface it on the section row so an agent can `Read`
// the file directly without a second query.
CALL db.index.fulltext.queryNodes($index_name, toInteger($k * 4))
YIELD node AS section, score
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
```

B.3 Document neighbourhood
```cypher
// Closest analog to A.3. `:LINKS_TO` replaces `:MENTIONS`; aliases replace
// redirects (wikilink resolution already happens at ingest, so we don't
// canonicalise here). Traversal goes through any element of the start doc
// (the doc itself or any of its sections) via `:HAS*0..`, then jumps
// across documents via `:LINKS_TO`, and projects each endpoint back to its
// owning Document.
//
// We use a *quantified path pattern* for the `:LINKS_TO` hop chain rather
// than legacy `*1..n` variable-length syntax — the legacy form requires a
// literal upper bound at parse time and so wouldn't accept `$n` either.
// The `{1,$n}` quantifier shown is a *template*: current Neo4j 5.x server
// versions (incl. Aura) reject Cypher parameters inside the quantifier, so
// the wrapper (`run_b3` in src/ki/search/queries.py) substitutes the literal
// int client-side before sending the query. Safe because the client-side
// code coerces `n` to int first.
//   https://neo4j.com/docs/cypher-manual/current/patterns/variable-length-paths/
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
```

B.4 Document text
```cypher
// Walks the document's NEXT_SECTION chain from first section to last. The
// first section is the direct child of the document with no incoming
// NEXT_SECTION edge. The `(start)-[:HAS*]->(section)` filter is
// defensive — keeps the walk inside this document if the chain ever
// accidentally crosses documents.
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
```

B.5 Document frontmatter and section titles
```cypher
// `frontmatter` (structured YAML metadata) replaces `infobox`; section
// displayNames replace section titles. Sections returned in strict DFS
// reading order via NEXT_SECTION. Row shape matches A.5: one row per
// "thing" (kind = 'frontmatter' | 'section title').
MATCH (start:Document {uri: $uri})
OPTIONAL MATCH (start)-[:HAS]->(first:Section)
WHERE NOT (:Section)-[:NEXT_SECTION]->(first)
OPTIONAL MATCH path = (first)-[:NEXT_SECTION*0..]->(section:Section)
WHERE (start)-[:HAS*]->(section)
WITH start, section, length(path) AS reading_order
ORDER BY reading_order
WITH start, collect({
  uri: section.uri,
  heading: section.displayName,
  heading_level: section.headingLevel
}) AS sections
WITH start, sections,
     CASE WHEN start.frontmatter IS NULL OR start.frontmatter = ''
          THEN []
          ELSE [{kind: 'frontmatter', uri: start.uri, text: start.frontmatter}]
     END AS fm_rows,
     [s IN sections WHERE s.uri IS NOT NULL
      | {kind: 'section title', uri: s.uri, text: s.heading}] AS section_rows
UNWIND (fm_rows + section_rows) AS row
RETURN row.kind AS kind, row.uri AS uri, row.text AS text
```

B.6 Get sections
```cypher
// Same input contract as A.6 (`$section_ids` → `$section_uris`). Sections own
// their content directly (no paragraph hop), so this is a flat lookup.
WITH $section_uris AS uris
UNWIND range(0, size(uris) - 1) AS i
WITH i, uris[i] AS section_uri
MATCH (section:Section {uri: section_uri})
RETURN i AS idx,
       section.uri AS section_uri,
       section.displayName AS heading,
       section.headingLevel AS heading_level,
       section.content AS content,
       section.path AS path
ORDER BY i
```

B.7 Windowing sections (full content)
```cypher
// Direct port of A.7 (paragraph) and A.8 (section) windowing — collapsed
// because the new model has no Paragraph. Walks ±N steps along
// NEXT_SECTION around the hit. Crosses heading levels (DFS order), same
// flat semantics A.7 had for paragraphs.
MATCH (hit:Section {uri: $section_uri})
OPTIONAL MATCH back_path = (hit)<-[:NEXT_SECTION*1..]-(prev:Section)
WHERE length(back_path) <= $n
WITH hit, collect({section: prev, offset: -length(back_path)}) AS backward
OPTIONAL MATCH fwd_path = (hit)-[:NEXT_SECTION*1..]->(next:Section)
WHERE length(fwd_path) <= $n
WITH hit, backward, collect({section: next, offset: length(fwd_path)}) AS forward
WITH [{section: hit, offset: 0}] + backward + forward AS all_entries
UNWIND all_entries AS e
WITH e.section AS section, e.offset AS offset
WHERE section IS NOT NULL
RETURN section.uri AS section_uri,
       offset,
       section.displayName AS heading,
       section.headingLevel AS heading_level,
       section.content AS content
ORDER BY offset
```

B.8 Windowing sections (summary)
```cypher
// Analog of A.8's summary form: same ±N NEXT_SECTION walk as B.7, but
// returns the *shape* of each section rather than its body — heading,
// first child section, and child count. Useful for "what's around this
// hit?" overviews without paying for the full content.
//
// `first_child` exploits the DFS property of NEXT_SECTION: a section's
// first child is the section it points to via NEXT_SECTION *that is also*
// a HAS child of it.
MATCH (hit:Section {uri: $section_uri})
OPTIONAL MATCH back_path = (hit)<-[:NEXT_SECTION*1..]-(prev:Section)
WHERE length(back_path) <= $n
WITH hit, collect({section: prev, offset: -length(back_path)}) AS backward
OPTIONAL MATCH fwd_path = (hit)-[:NEXT_SECTION*1..]->(next:Section)
WHERE length(fwd_path) <= $n
WITH hit, backward, collect({section: next, offset: length(fwd_path)}) AS forward
WITH [{section: hit, offset: 0}] + backward + forward AS all_entries
UNWIND all_entries AS e
WITH e.section AS section, e.offset AS offset
WHERE section IS NOT NULL
OPTIONAL MATCH (section)-[:NEXT_SECTION]->(first_child:Section)
WHERE (section)-[:HAS]->(first_child)
OPTIONAL MATCH (section)-[:HAS]->(child:Section)
WITH section, offset, first_child, count(child) AS child_count
RETURN section.uri AS section_uri,
       offset,
       section.displayName AS heading,
       section.headingLevel AS heading_level,
       first_child.uri AS first_child_section_uri,
       first_child.displayName AS first_child_heading,
       child_count
ORDER BY offset
```

B.9 Get backlinks
```cypher
// Sources pointing TO the target document or any of its sections via LINKS_TO.
// Each source is projected back to its owning document so callers get
// "which document mentions me, and in which section."
MATCH (target:Document {uri: $uri})
OPTIONAL MATCH (target)-[:HAS*]->(targetSection:Section)
WITH collect(DISTINCT target) + collect(DISTINCT targetSection) AS targets
UNWIND targets AS tgt
MATCH (src)-[link:LINKS_TO]->(tgt)
WHERE src:Document OR src:Section
OPTIONAL MATCH (srcDoc:Document)-[:HAS*]->(src)
WHERE src:Section
WITH coalesce(srcDoc, src) AS source_document,
     src AS source_element,
     tgt,
     link
WHERE source_document:Document AND source_document.uri <> $uri
RETURN source_document.uri AS source_document_uri,
       source_document.displayName AS source_title,
       source_element.uri AS source_element_uri,
       source_element.content AS source_content,
       tgt.uri AS target_uri,
       link.wikilink AS is_wikilink,
       link.embed AS is_embed
ORDER BY source_document_uri, source_element_uri
```

B.10 Shortest path
```cypher
// Shortest LINKS_TO chain between two documents, with per-hop evidence
// (the section snippet that contains the outbound link). Mirrors A.10:
// `:MENTIONS` → `:LINKS_TO`, articles → documents. We allow the path to
// start from either the document node or any of its sections, since
// LINKS_TO sources are mixed.
MATCH (a:Document {uri: $uri_a})
MATCH (b:Document {uri: $uri_b})
MATCH (a)-[:HAS*0..]->(aElem)
MATCH (b)-[:HAS*0..]->(bElem)
WITH a, b, aElem, bElem
MATCH path = shortestPath((aElem)-[:LINKS_TO*..6]->(bElem))
WITH path
ORDER BY length(path)
LIMIT 1
WITH path, nodes(path) AS elems
UNWIND range(0, size(elems) - 2) AS i
WITH elems[i] AS fromElem, elems[i+1] AS toElem, i
OPTIONAL MATCH (fromDoc:Document)-[:HAS*]->(fromElem)
WHERE fromElem:Section
OPTIONAL MATCH (toDoc:Document)-[:HAS*]->(toElem)
WHERE toElem:Section
WITH i,
     coalesce(fromDoc, fromElem) AS from_document,
     coalesce(toDoc, toElem) AS to_document,
     fromElem
RETURN i AS hop,
       from_document.uri AS from_document_uri,
       from_document.displayName AS from_title,
       to_document.uri AS to_document_uri,
       to_document.displayName AS to_title,
       fromElem.uri AS evidence_element_uri,
       fromElem.content AS evidence_content
ORDER BY hop
```


B.11 Vault fulltext search
```cypher
// New in v0.4.0. Same shared `content_search` index as B.1/B.2; filter to
// `:Vault` so we only surface vault-level results. The `description` field
// is user-authored (read from `.ki/vault.yaml` on each ingest), so this is
// the routing query an agent runs to pick the right vault for a topic
// *before* drilling into doc/section search scoped to that vault.
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
```


B.12 Containment tree (hierarchy walk)
```cypher
// New in v0.4.0. `ki outline`'s HAS walker (formerly `ki tree`). Returns the root row + one row per
// HAS-descendant up to $depth steps. The full wire format is defined in
// `docs/outline-format.md` *Wire record format* — this query is its only producer.
//
// Sections additionally carry `sort_pos` (their index in the parent document's
// NEXT_SECTION chain) so the renderer can order sibling sections by reading
// order rather than alphabetically.
//
// When $root_uri is null/absent, every :Vault in the graph is matched as a
// root and the walk fans out from each. The renderer treats multi-root
// output as a sibling group at parent_uri=null sorted alphabetically by name.
// Multi-user scoping via :USES_VAULT is a follow-up — single-user makes
// "all vaults" unambiguous today.
//
// Outbound :LINKS_TO edges are surfaced by B.12-links — this query is HAS-only.
//
// $root_uri — Vault / Folder / Document / Section URI, or null to match all
//             :Vault nodes as roots.
// $depth    — max HAS steps from each root (must be >= 1).
//
// We use a **quantified path pattern** for the `:HAS` hop chain rather than
// legacy `*1..n` variable-length syntax — legacy `*m..n` requires literal
// upper bounds, and `*1..` with a post-filter (`WHERE length(path) <= $depth`)
// is a serious perf trap because it walks the entire reachable subgraph and
// only then prunes. The `{1,$depth}` quantifier prunes during traversal —
// but current Neo4j 5.x (incl. Aura) rejects Cypher parameters inside the
// quantifier, so the wrapper (`run_b12` once `ki outline` lands in phase 3 of
// #17) must substitute the literal int client-side, same as B.3.
//   https://neo4j.com/docs/cypher-manual/current/patterns/variable-length-paths/
MATCH (root)
WHERE ($root_uri IS NOT NULL
       AND root.uri = $root_uri
       AND (root:Vault OR root:Folder OR root:Document OR root:Section))
   OR ($root_uri IS NULL AND root:Vault)

CALL (root) {
  // Root row.
  RETURN 0                                       AS depth,
         null                                    AS inrel,
         labels(root)[0]                         AS label,
         coalesce(root.name, root.displayName)   AS name,
         root.displayName                        AS displayName,
         root.uri                                AS uri,
         null                                    AS parent_uri,
         null                                    AS sort_pos

  UNION

  // HAS descendants, up to $depth steps from root.
  MATCH path = (root) (()-[:HAS]->()){1,$depth} (d)
  // For Section descendants, find the unique start of the doc's NEXT_SECTION
  // chain and compute d's position in it. firstSec has no incoming
  // NEXT_SECTION. For non-Section d, nsp stays null.
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
```

B.12-links Outbound LINKS_TO sub-pass
```cypher
// New in v0.4.0. Outbound :LINKS_TO edges from a set of source URIs.
// Called by `ki outline` after B.12 to surface horizontal LINKS_TO branches
// (one row per outbound edge, source identified by parent_uri).
//
// The renderer combines these rows with the B.12 hierarchy rows, sets
// `depth = source_depth + 1` and `inrel = 'LINKS_TO'`, and sorts L siblings
// alphabetically by target uri. See `docs/outline-format.md` *Renderer pseudocode*.
//
// $source_uris — list of :Document and :Section URIs from the B.12 result.
UNWIND $source_uris AS source_uri
MATCH (src {uri: source_uri})-[:LINKS_TO]->(tgt)
WHERE src:Document OR src:Section
RETURN src.uri                              AS parent_uri,
       labels(tgt)[0]                       AS label,
       coalesce(tgt.name, tgt.displayName)  AS name,
       tgt.displayName                      AS displayName,
       tgt.uri                              AS uri
ORDER BY parent_uri, uri
```

B.13 Node lookup (any URI, label-aware metadata)
```cypher
// New in v0.4.0. `ki get`'s metadata reader. Returns the node's label
// plus all of its properties in a single bag — the dispatcher in
// `src/ki/commands/get.py` picks the label-relevant subset client-side.
//
// `:Folder` and `:Vault` URIs also match this query — the dispatcher
// surfaces them with an error pointing at `ki outline` / `ki vault list`.
// Returning `label` lets the dispatcher reject Folder/Vault cleanly
// without a second round trip.
//
// Why `properties(n)` instead of naming columns: per-label optional
// properties (`Document.frontmatter`, `Document.aliases`, ...) trigger a
// `01N52` "property does not exist" notification when the DB has no
// node that's ever been written with that key — common on small vaults
// with no custom frontmatter. `properties(n)` returns only the keys
// that actually exist on the node, so no spurious warnings. The shape
// client-side is unchanged: `row.get("frontmatter")` returns the value
// or `None` exactly as before.
//
// The `content` field (per Content Construction Rule 1: preamble + URI
// pointers to direct children) is what `ki get --type content` emits
// as-is. `--type path` drops it; `--type full` replaces it with a
// reconstructed reading-order body via B.4 (Document) or B.14 (Section).
MATCH (n {uri: $uri})
RETURN labels(n)[0] AS label, properties(n) AS props
```

B.14 Section text with subtree
```cypher
// New in v0.4.0. Section-side analog of B.4. Used by `ki get --type full`
// when the URI resolves to a `:Section`. Walks the document's NEXT_SECTION
// chain starting from the given section, bounded to the subtree under
// `start` via `start = s OR (start)-[:HAS*]->(s)`. The NEXT_SECTION walk
// may continue past the subtree before the WHERE filter prunes — that's
// OK, bounded by doc section count and resolved in a single query.
//
// Why this instead of just B.4 on the owning Document: an H1 with deep
// H2/H3 children should return just that subtree, not the whole doc.
// `ki get <h2-uri> --type full` returns the H2's body + its H3
// descendants in reading order, nothing else.
MATCH (start:Section {uri: $uri})
MATCH path = (start)-[:NEXT_SECTION*0..]->(s:Section)
WHERE start = s OR (start)-[:HAS*]->(s)
RETURN s.uri          AS section_uri,
       s.displayName  AS heading,
       s.headingLevel AS heading_level,
       s.content      AS content,
       length(path)   AS reading_order
ORDER BY reading_order
```
