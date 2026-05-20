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
- `$uri` — the `Document.uri` (UUID-prefixed slugified path, see §4.3).
- `$section_uri`, `$section_uris` — same convention for sections.
- `$folder_uri` — a `Folder.uri` (UUID-prefixed slugified directory path) used as a prefix in `--under` scoping for B.1/B.2/B.11 — see *Scoping* below.
- `$root_uri` — for B.12 / `ki tree`; URI of any node to start the tree walk from (`:Vault`, `:Folder`, `:Document`, or `:Section`).
- `$index_name` — `'content_search'` (the fulltext index from §4.4).
- `$query` — fulltext query string (Lucene syntax).
- `$k`, `$n` — limit / window size.
- `$depth` — for B.12 / `ki tree`; cap on tree traversal depth.

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
- **B.12 Containment tree** — pure graph walk over `:HAS` (the universal containment edge), depth-capped via a **quantified path pattern** quantifier (`{1,$depth}`) so the depth bound prunes during traversal rather than as a post-filter — same trick as B.3. Powers `ki tree`. Starts from any `:Vault`, `:Folder`, `:Document`, or `:Section` URI (`$root_uri`), so the same query renders the whole vault, a folder subtree, the heading tree of one document, or the sub-headings under a heading. The caller decides which `kind`s to keep (e.g. `ki tree` against a `:Vault` root hides `:Section` rows for a directory-style view).


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
       score
ORDER BY score DESC
LIMIT toInteger($k)
```

B.2 Section content fulltext search
```cypher
// Section is the smallest content unit in the new model (no Paragraph / Chunk).
// Roll each section hit up to its owning document so callers get both
// granularities — the section for retrieval, the document for context.
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
// than legacy `*1..n` variable-length syntax, because legacy variable-length
// requires a literal upper bound; quantified path patterns accept a parameter
// in the quantifier. See:
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
       section.content AS content
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


B.12 Containment tree (depth-capped)
```cypher
// New in v0.4.0. Walks the containment tree from any `$root_uri` outward via
// the single `HAS` relationship type. Roots can be a `:Vault`, `:Folder`,
// `:Document`, or `:Section` — same query for "the whole vault as a tree",
// "this folder's subtree", "the heading tree of one doc", or "everything
// nested under this heading".
//
// Pure graph walk — no fulltext, no scoring. Returns one row per descendant
// with its parent URI and depth from the root; the caller (`ki tree`)
// rebuilds the tree client-side and decides how to render (directory-style
// in the terminal, YAML with `--out-file`, etc.) and which kinds to include
// (e.g. `ki tree` filters Sections out by default for a folder root).
//
// $root_uri — a Vault / Folder / Document / Section URI.
// $depth    — cap on traversal depth from the root (must be >= 1).
//
// We use a **quantified path pattern** for the `:HAS` hop chain rather than
// legacy `*1..n` variable-length syntax — legacy `*m..n` requires literal
// upper bounds, and `*1..` with a post-filter (`WHERE length(path) <= $depth`)
// is a serious perf trap because it walks the entire reachable subgraph and
// only then prunes. Quantified path patterns accept the depth parameter *in
// the quantifier* and prune during traversal. Same trick as B.3.
//   https://neo4j.com/docs/cypher-manual/current/patterns/variable-length-paths/
MATCH (root {uri: $root_uri})
WHERE root:Vault OR root:Folder OR root:Document OR root:Section
MATCH path = (root) (()-[:HAS]->()){1,$depth} (child)
RETURN child.uri AS uri,
       labels(child)[0] AS kind,
       child.displayName AS display_name,
       [n IN nodes(path)[-2..-1] | n.uri][0] AS parent_uri,
       length(path) AS depth
ORDER BY depth, parent_uri, display_name
```
