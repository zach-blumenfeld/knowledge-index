A.1 Title vector search
CALL db.index.vector.queryNodes($index_name, toInteger($k), $query_vector)
YIELD node as article, score
RETURN article.nodeID AS article_nodeID,
article.title AS title,
score
ORDER BY score DESC

A.2 Chunk vector search
CALL db.index.vector.queryNodes($index_name, toInteger($k * 4), $query_vector)
YIELD node AS chunk, score
MATCH (para:Paragraph)-[:HAS_CHUNK]->(chunk)
WITH para, chunk, score
ORDER BY score DESC
WITH para, head(collect({chunk_id: chunk.nodeID, score: score})) AS best_chunk
ORDER BY best_chunk.score DESC
LIMIT toInteger($k)
RETURN para.nodeID AS paragraph_nodeID,
para.text AS text,
best_chunk.chunk_id AS matched_chunk_id,
best_chunk.score AS score

A.3 Article neighbourhood
MATCH (start:Article {title: $title})
OPTIONAL MATCH (start)-[:REDIRECTS_TO*1..10]->(t:Article)
WHERE NOT (t)-[:REDIRECTS_TO]->()
WITH coalesce(t, start) AS canonical
MATCH path = (canonical)-[:MENTIONS*1..$n]->(neighbour:Article)
WHERE neighbour <> canonical
WITH canonical, neighbour, min(length(path)) AS distance
OPTIONAL MATCH (neighbour)-[:REDIRECTS_TO*1..10]->(nt:Article)
WHERE NOT (nt)-[:REDIRECTS_TO]->()
WITH canonical.title AS source, coalesce(nt, neighbour) AS resolved_node, distance
WHERE resolved_node.title <> source
RETURN resolved_node.title AS title,
min(distance) AS distance
ORDER BY distance, title

A.4 Article text
MATCH (start:Article {title: $title})
OPTIONAL MATCH (start)-[:REDIRECTS_TO*1..10]->(t:Article)
WHERE NOT (t)-[:REDIRECTS_TO]->()
WITH coalesce(t, start) AS canonical
MATCH (canonical)-[:HAS_SECTION]->(first_section:Section)
WHERE NOT (:Section)-[:NEXT_SECTION]->(first_section)
MATCH section_path = (first_section)-[:NEXT_SECTION*0..]->(section:Section)
WITH canonical, section, length(section_path) AS section_idx
MATCH (section)-[:HAS_PARAGRAPH]->(first_para:Paragraph)
WHERE NOT EXISTS {
(section)-[:HAS_PARAGRAPH]->(:Paragraph)-[:NEXT_PARAGRAPH]->(first_para)
}
MATCH para_path = (first_para)-[:NEXT_PARAGRAPH*0..]->(para:Paragraph)
WHERE (section)-[:HAS_PARAGRAPH]->(para)
RETURN para.nodeID as nodeID,
para.text AS text
ORDER BY section_idx, length(para_path)

A.5 Article infoboxes and section titles
MATCH (start:Article {title: $title})
OPTIONAL MATCH (start)-[:REDIRECTS_TO*1..10]->(t:Article)
WHERE NOT (t)-[:REDIRECTS_TO]->()
WITH coalesce(t, start) AS canonical
CALL (canonical) {
MATCH (canonical)-[:HAS_SECTION]->(first_section:Section)
WHERE NOT (:Section)-[:NEXT_SECTION]->(first_section)
MATCH section_path = (first_section)-[:NEXT_SECTION*0..]->(section:Section)
WITH section, length(section_path) AS section_idx
MATCH (section)-[:HAS_PARAGRAPH]->(first_para:Paragraph)
WHERE NOT EXISTS {
(section)-[:HAS_PARAGRAPH]->(:Paragraph)-[:NEXT_PARAGRAPH]->(first_para)
}
OPTIONAL MATCH (first_para)-[:HAS_CHUNK]->(fallback_chunk:Chunk)
WHERE NOT (fallback_chunk)-[:PREVIOUS_CHUNK]->(:Chunk)
AND NOT first_para.text CONTAINS '{{Infobox'
WITH section, section_idx, fallback_chunk,
[line IN split(first_para.text, '\n')
WHERE line =~ '\\s*=+[^=]+=+\\s*'
| trim(replace(line, '=', ''))] AS heading_titles
WITH section, section_idx,
CASE
WHEN size(heading_titles) > 0 THEN heading_titles
WHEN fallback_chunk IS NOT NULL THEN [fallback_chunk.text]
ELSE []
END AS titles
UNWIND range(0, size(titles) - 1) AS title_idx
WITH section_idx, title_idx,
{nodeID: section.nodeID, title: titles[title_idx]} AS row
ORDER BY section_idx, title_idx
RETURN collect(row) AS sections
}
CALL (canonical) {
MATCH (canonical)-[:HAS_SECTION]->(first_section:Section)
WHERE NOT (:Section)-[:NEXT_SECTION]->(first_section)
MATCH section_path = (first_section)-[:NEXT_SECTION*0..]->(section:Section)
WITH section, length(section_path) AS section_idx
MATCH (section)-[:HAS_PARAGRAPH]->(first_para:Paragraph)
WHERE NOT EXISTS {
(section)-[:HAS_PARAGRAPH]->(:Paragraph)-[:NEXT_PARAGRAPH]->(first_para)
}
MATCH para_path = (first_para)-[:NEXT_PARAGRAPH*0..]->(para:Paragraph)
WHERE (section)-[:HAS_PARAGRAPH]->(para)
WITH section_idx, length(para_path) AS para_idx, para
ORDER BY section_idx, para_idx
WITH collect(DISTINCT para.text) AS texts
WITH reduce(s = "", t IN texts |
CASE WHEN s = "" THEN t ELSE s + "\n" + t END) AS doc
WITH doc, split(doc, "{{Infobox") AS parts
UNWIND range(1, size(parts) - 1) AS k
WITH doc, parts, k,
reduce(off = 0, j IN range(0, k-1) | off + size(parts[j])) + 9 * (k - 1) AS start
WITH doc, start, split(substring(doc, start), "}}") AS segs
WITH doc, start, segs,
reduce(st = {idx:-1, opens:0, found:false}, k IN range(0, size(segs) - 2) |
CASE
WHEN st.found
THEN st
WHEN st.opens + size(split(segs[k], "{{")) - 1 = k + 1
THEN {idx:k, opens: st.opens + size(split(segs[k], "{{")) - 1, found:true}
ELSE {idx:-1, opens: st.opens + size(split(segs[k], "{{")) - 1, found:false}
END) AS r
WHERE r.found
WITH doc, start, segs, r,
reduce(s = 0, k IN range(0, r.idx) | s + size(segs[k])) + 2 * (r.idx + 1) AS endOffset
WITH start, substring(doc, start, endOffset) AS infobox
ORDER BY start
RETURN collect(infobox) AS infoboxes
}
UNWIND (
[ib IN infoboxes | {kind: 'infobox', nodeID: NULL, text: ib}] +
[s IN sections | {kind: 'section title', nodeID: s.nodeID, text: s.title}]
) AS row
RETURN row.kind AS kind, row.nodeID AS nodeID, row.text AS text

A.6 Get sections
WITH $section_ids AS ids
UNWIND range(0, size(ids) - 1) AS i
WITH i, ids[i] AS sid
MATCH (section:Section {nodeID: sid})
MATCH (section)-[:HAS_PARAGRAPH]->(first_para:Paragraph)
WHERE NOT EXISTS {
(section)-[:HAS_PARAGRAPH]->(:Paragraph)-[:NEXT_PARAGRAPH]->(first_para)
}
MATCH para_path = (first_para)-[:NEXT_PARAGRAPH*0..]->(para:Paragraph)
WHERE (section)-[:HAS_PARAGRAPH]->(para)
RETURN section.nodeID AS section_nodeID,
para.nodeID AS paragraph_nodeID,
para.text AS text
ORDER BY i, length(para_path)

A.7 Windowing paragraphs
MATCH (hit:Paragraph {nodeID: $paragraph_id})
OPTIONAL MATCH back_path = (hit)<-[:NEXT_PARAGRAPH*1..]-(prev:Paragraph)
WHERE length(back_path) <= $n
WITH hit, collect({para: prev, offset: -length(back_path)}) AS backward
OPTIONAL MATCH fwd_path = (hit)-[:NEXT_PARAGRAPH*1..]->(next:Paragraph)
WHERE length(fwd_path) <= $n
WITH hit, backward, collect({para: next, offset: length(fwd_path)}) AS forward
WITH [{para: hit, offset: 0}] + backward + forward AS all_entries
UNWIND all_entries AS e
WITH e.para AS para, e.offset AS offset
WHERE para IS NOT NULL
RETURN para.nodeID AS paragraph_nodeID,
offset,
para.text AS text
ORDER BY offset

A.8 Windowing sections
MATCH (hit:Section {nodeID: $section_id})
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
// First paragraph of the section
OPTIONAL MATCH (section)-[:HAS_PARAGRAPH]->(first_para:Paragraph)
WHERE NOT EXISTS {
(section)-[:HAS_PARAGRAPH]->(:Paragraph)-[:NEXT_PARAGRAPH]->(first_para)
}
// Paragraph count
OPTIONAL MATCH (section)-[:HAS_PARAGRAPH]->(p:Paragraph)
RETURN section.nodeID AS section_nodeID,
offset,
first_para.nodeID AS first_paragraph_nodeID,
first_para.text AS first_paragraph_text,
count(p) AS paragraph_count
ORDER BY offset

A.9 Get backlinks
MATCH (start:Article {title: $title})
OPTIONAL MATCH (start)-[:REDIRECTS_TO*1..10]->(t:Article)
WHERE NOT (t)-[:REDIRECTS_TO]->()
WITH coalesce(t, start) AS target
MATCH (para:Paragraph)-[:LINKS_TO]->(target)
MATCH (src:Article)-[:HAS_SECTION]->(section:Section)-[:HAS_PARAGRAPH]->(para)
WHERE src <> target
RETURN src.title AS source_article,
para.nodeID AS paragraph_nodeID,
para.text AS text
ORDER BY source_article

A.10 Shortest path
// Canonicalise both endpoints
MATCH (start_a:Article {title: $title_a})
OPTIONAL MATCH (start_a)-[:REDIRECTS_TO*1..10]->(ta:Article)
WHERE NOT (ta)-[:REDIRECTS_TO]->()
WITH coalesce(ta, start_a) AS a
MATCH (start_b:Article {title: $title_b})
OPTIONAL MATCH (start_b)-[:REDIRECTS_TO*1..10]->(tb:Article)
WHERE NOT (tb)-[:REDIRECTS_TO]->()
WITH a, coalesce(tb, start_b) AS b
// Shortest path via the projected MENTIONS edge
// There are longer chains in Wikipedia, but 6 feels good (Six degrees of separation)
MATCH path = shortestPath((a)-[:MENTIONS*..6]->(b))
// Extract each (from -> to) pair along the path
WITH path, nodes(path) AS articles, range(0, length(path) - 1) AS idx
UNWIND idx AS i
WITH articles[i] AS from_article, articles[i+1] AS to_article, i
// Find a paragraph in from_article that links to to_article
MATCH (from_article)-[:HAS_SECTION]->(section:Section)-[:HAS_PARAGRAPH]->(para:Paragraph)
-[:LINKS_TO]->(to_target:Article)
WHERE to_target = to_article
OR EXISTS {
MATCH (to_target)-[:REDIRECTS_TO*1..10]->(to_article)
}
WITH i, from_article, to_article, section, para
ORDER BY i, size(para.text) // prefer shorter, more focused paragraphs
WITH i, from_article, to_article, head(collect({
section_id: section.nodeID, paragraph_id: para.nodeID, text: para.text
})) AS evidence
RETURN i AS hop,
from_article.title AS from_article,
to_article.title AS to_article,
evidence.paragraph_id AS paragraph_nodeID,
evidence.text AS paragraph_text
ORDER BY hop