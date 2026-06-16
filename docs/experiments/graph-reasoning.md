# Graph-reasoning prompting — experiment log

**Question under test:** does framing an agent *reasoning-first* ("read the schema,
reason about paths/patterns, then express in Cypher") produce better graph queries
than *query-first* ("write a Cypher query to answer X")? This is the claim behind the
SKILL's graph-reasoning sections (the `<!-- TODO -->` reframe). We test it empirically
against a real graph rather than asserting it.

This is a **research log**, not a spec — it informs how `skills/knowledge-base/SKILL.md`
should frame the `neo4j-cli` delegation.

## Setup

- **Graph:** the `content-research` profile (the author's research wiki) — **187
  Documents, 940 `LINKS_TO` edges**. Rich enough for centrality / path reasoning.
- **Tooling:** subagents query via `neo4j-cli query "<cypher>" --credential
  content-research` (read-only). `neo4j-cli`'s own agent skills were installed
  (`neo4j-cli skill install --all`), and agents could self-discover via
  `neo4j-cli agent-context` + `query :schema`.
- **Method:** spawn one subagent per (question × framing); each reports schema-read,
  the queries it ran, its answer, and a self-assessment. Framing is embedded in the
  task wording (agents are *not* told which arm they're in).

## Iteration 1 — 2 questions × 2 framings (2026-06-15)

Framings:
- **query-first:** "Write Cypher queries (via neo4j-cli) to answer: <Q>."
- **reasoning-first:** "Don't start with a query — read the schema, reason about the
  nodes/relationships/paths that reveal the answer (think traversal, neighborhoods,
  centrality, paths — not table lookups), then express it as Cypher and iterate."

Questions (both need *structure*, where framing should bite):
- **Q1:** "What's load-bearing — which notes should I read first?"
- **Q2:** "What's the throughline connecting the vector / semantic-search notes to the
  AIP project?"

### Records

| Arm | schema first? | # queries | approach | outcome |
|---|---|---|---|---|
| Q1 query-first | yes | 3 | in-degree centrality over `Section→Document LINKS_TO` | solid ranked hub list (`blog-neo4j-cli`, `semantic-search-without-vectors`, `the-partitioning-thesis`, `aip`…) |
| Q1 reasoning-first | yes | 4 | in-**and**-out-degree; **split navigational sinks (`index.md`, `CLAUDE.md`) from load-bearing *ideas*** | richer, tiered answer; self-noted degree-as-proxy (no true PageRank) |
| Q2 query-first | yes | 5 | `shortestPath` + bridging-theme neighborhood | crisp: bridge = theme **`avoid-llm-at-ingest`** (+ `compile-dont-rederive`) |
| Q2 reasoning-first | yes | 8 | `shortestPath` + section-mediated links + shared-neighbor intersection | bridge = **`blog-karpathy`** + graph-shape/compile themes; more exploration, not clearly better |

### Findings

1. **The predicted query-first failure mode barely appeared.** *Every* arm read
   `:schema` first and used graph structure (centrality, `shortestPath`,
   neighborhoods) — none fell back to flat counts or keyword matching.
2. **Reasoning-first added real nuance on Q1** — it distinguished *navigational* hubs
   (high out-degree, zero in-degree = table-of-contents pages) from *load-bearing
   ideas*, a tiering query-first missed. Genuine qualitative win.
3. **…but cost more and wasn't uniformly better.** Reasoning-first ran more queries
   (4 & 8 vs 3 & 5); on Q2 the query-first answer was arguably crisper.
4. **Confound — `neo4j-cli`'s own skills already do much of the reframe.** Its
   `query` help and installed skills enforce "*inspect `:schema` first, never guess
   it*" and graph-shaped querying. So even a bare "write a Cypher query" prompt
   inherited schema-first, structure-aware behavior. **The tool scaffolds the
   reasoning-first stance.**

### Implication for the SKILL

The heavy reframe may be **partly redundant with what `neo4j-cli` already installs**.
Leaning candidate: ki's SKILL should **delegate to `neo4j-cli` (+ its skills) and add a
light reasoning-first nudge**, not re-teach the whole stance. Confirm before rewriting.

### Threats to validity / next iteration

- The query-first framing here was **mild** ("write Cypher to answer X"), not the
  naive "translate to one SQL-style query" the TODO warns about. **Iteration 2: add a
  genuinely naive control arm.**
- One run, 2 questions, n=1 agent per cell — **no statistical weight.** Scale to more
  questions and ≥2 agents per cell.
- The `neo4j-cli`-skills confound is real and probably *desirable* (it's how the tool
  ships) — but to isolate the framing effect, a future run could compare with the
  skills' schema-first guidance suppressed.
