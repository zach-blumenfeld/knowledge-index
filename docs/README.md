# `ki` documentation

`ki` (CLI for `knowledge-index`) indexes a folder of markdown into a queryable
Neo4j knowledge graph. This is the docs index — start at the top.

## Start here

- [general-philosophy.md](general-philosophy.md) — the non-negotiable design
  principles (an index, not a document store; safe by default).
- [scoping.md](scoping.md) — profiles, vaults, config, the command surface, and
  the local/remote scoping model. The keystone doc.
- [architecture.md](architecture.md) — end-to-end overview: layers, the graph,
  write/read paths. Read this first.
- [skills.md](skills.md) — the agent skill(s): the shipped `knowledge-base` skill
  and the planned `knowledge-base-reader`.

## Commands — [`commands/`](commands/)

Per-command depth docs.

- [commands/search.md](commands/search.md) — `ki search`: the unified Document +
  Section fulltext sweep, Lucene syntax, and scope.
- [commands/get.md](commands/get.md) — `ki get`: fetch content by uri
  (`--type path` / `content` / `full`).
- [commands/outline.md](commands/outline.md) — `ki outline` (alias `ki tree`): the
  rendered containment outline and its scoping.
- [commands/add-rm.md](commands/add-rm.md) — `ki add` / `ki rm`: the incremental
  write surface (index-only; never touches disk), the rename workflow, and why
  there's no `ki mv`.
- [commands/theme-format.md](commands/theme-format.md) — `ki theme` output format.
  **Draft — not yet shipped.**

## Data model & internals — [`data-model/`](data-model/)

The graph, how it's built, and how it's queried.

- [data-model/schema.md](data-model/schema.md) — Neo4j schema: nodes, edges,
  properties, uri conventions, content-construction rules. **Normative.**
- [data-model/ingest-cypher.md](data-model/ingest-cypher.md) — batched ingest
  Cypher, MERGE strategy, constraints + fulltext index.
- [data-model/retrieval-queries.md](data-model/retrieval-queries.md) — the B.1–B.14
  retrieval query shapes.
- [data-model/link_capture.md](data-model/link_capture.md) — link classification,
  resolution, and the three Document kinds.
- [data-model/index_rm_behavior.md](data-model/index_rm_behavior.md) — vault-level
  sync + removal behavior (`ki drop` / re-index = nuke-and-rebuild).
- [data-model/theme-queries.md](data-model/theme-queries.md) — GDS Leiden
  clustering for themes. **Draft — not yet shipped.**

## Experiments — [`experiments/`](experiments/)

Empirical research logs that inform design decisions (not specs).

- [experiments/graph-reasoning.md](experiments/graph-reasoning.md) — query-first vs
  reasoning-first prompting for `neo4j-cli` graph queries, run against the live wiki
  graph. Informs the SKILL's graph-reasoning framing.

## Archive — [`archive/`](archive/)

Historical / superseded design records. Kept for provenance — **not current truth.**

- [archive/requirements_v01_mvp.md](archive/requirements_v01_mvp.md) — the original
  v0.1 MVP spec (superseded by scoping / general-philosophy / schema / commands).
- [archive/discussion-vector-indexing.md](archive/discussion-vector-indexing.md) —
  deferred deliberation on vector retrieval.
- [archive/research-data-model/](archive/research-data-model/) — pre-rewrite
  research-phase queries.
- [archive/v0_3_0_semantic_search/](archive/v0_3_0_semantic_search/) — v0.3.0
  release spec.
- [archive/v0_3_1_introspect_dedup/](archive/v0_3_1_introspect_dedup/) — v0.3.1
  deliberation.
- [archive/workflow/](archive/workflow/) — feature-pipeline prompt templates.
