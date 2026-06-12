# Theme format for `ki theme`

**Status: draft — format only.** How themes are computed (clustering, GDS vs not, ingest vs read time) is out of scope; this pins down what the output looks like in a context window. Companion to `docs/outline-format.md`; reuses its row conventions.

**Design rule: self-explanatory like outline.** No jargon labels that need a guide. Every label is a plain-English phrase that defines its own content, and every count carries its unit inline (`in 31 docs`, never `×31`). A reader (human or agent) seeing the output cold should have no open questions about what a line means.

`ki` never names a theme (no LLM at ingest/read; #17 rules). Each block carries the evidence an agent needs to name the theme itself, plus full URIs to drill with (`ki outline <uri>` / `ki get <uri>`, verbatim round-trip — same contract as outline).

`ki theme` writes to stdout. Pipe to save; no output-format flag.

## Layout

### Header

```
THEMES  <vault-name>   <N> docs · <G> grouped into <K> themes by wikilinks · <U> ungrouped
```

- **`by wikilinks`** is the method statement, in plain words. If the producer degrades (link-sparse vault, capability missing), the phrase changes — e.g. `grouped into 6 themes by folders` — so differently-derived clusters are never presented identically.
- **`<U> ungrouped`** is always printed, even when 0 — it stops an agent from overclaiming coverage (`187 grouped` ≠ "the vault is about these K things"). `N = G + U` always reconciles; the reader can derive coverage % themselves, so none is printed.
- **`· <E> excluded`** is appended only when the run excluded docs (hub pages like `CLAUDE.md` / `index.md` dropped from the whole analysis via `--exclude`). Excluded docs leave the `<N>` denominator entirely — the segment exists so that removal is visible, never silent.
- Doc counts cover internal md docs only; stubs are excluded.

Header is always printed, even with zero themes (`0 grouped into 0 themes · N ungrouped`).

### Theme block

One block per theme, blank-line separated:

```
T1  52 docs (24%) · tightly interlinked
    top wikilink targets   [[GraphRAG]] in 31 docs · [[Lucene]] in 12 · [[chunking]] in 9
    most-linked docs       GraphRAG indexing cost .......... D   vault://abc-123/research/graphrag-cost.md
                           Lucene vs vectors ............... D   vault://abc-123/research/lucene-vs-vectors.md
                           (+50 more docs)
    links into T3 via      ki-design.md ..................... D   vault://abc-123/projects/ki-design.md
```

Line by line:

| Line | Required | Content |
|------|----------|---------|
| Theme line | yes | `T<id>  <docs> docs (<pct>%) · <cohesion>`. `T<id>` is the theme's id — referenceable in this output (the `links into T3` line) and in future flags (`ki theme --members T1`); stable within one index generation. Percent is of the header's `<N>`. Cohesion is a word, not a number — see below. |
| `top wikilink targets` | yes | The `[[wikilink]]` targets most linked from docs *inside* this theme, descending; `in <n> docs` = how many of the theme's docs link to that target. The `[[…]]` rendering shows the label is the user's own wikilink text — these are the user's words, not ki's. Default 3 entries (cap 5). Primary naming signal; survives journal vaults where every title is a date. Under folder grouping this line reads `top wikilink targets   (none — themes grouped by folders)`. |
| `most-linked docs` | yes | The theme's documents with the most links to/from other docs in the theme, descending — the label *is* the selection rule. Default 3 rows (`--per-theme` widens), each in the outline row shape (dots, `T` letter, full URI). Followed by `(+N more docs)` whenever membership exceeds the rows shown — membership is never silently truncated; rows + remainder always reconcile to the theme line's doc count. |
| `links into T<j> via` | only when non-empty | One row per crossover document — a doc in this theme whose wikilinks point at docs in theme `T<j>`. The row is the crossover doc itself (outline row shape, full URI), so "how do these themes connect" comes with its drill handle attached. One line per connected theme, ascending `T<j>`. |

### Cohesion word

The theme line ends with one of three phrases: **`tightly interlinked` · `moderately interlinked` · `loosely interlinked`**. It answers "is this a real cluster or a loose pile?" without exposing a score (AGENTS.md principle #2: backend opaque; a modularity float would be noise the agent can't act on). The renderer maps an internal density ratio to the three phrases; thresholds live with the producer spec, not here, and may be tuned freely — the output contract is only the three phrases.

### Doc rows

`most-linked docs` and `links into …` rows reuse `outline-format.md` *Row format* exactly: `displayName + dotted leader + <T> + full URI`. `<T>` is `D` in v1 (document-level themes; if section-level themes land, `S` rows follow the same shape). URIs are never truncated; the name side truncates at the 48-char cap with `…`, per outline-format *Truncation*.

## Ordering

| What | Sort key |
|------|----------|
| Theme blocks | Doc count descending. `T<id>` is assigned in that order at render time, so blocks always read T1, T2, T3… top to bottom. Ties broken by smallest member URI (stable across runs). |
| `top wikilink targets` | Linking-doc count descending; ties alphabetical. |
| `most-linked docs` | Within-theme link count descending; ties alphabetical by URI. |
| `links into …` lines | Target theme id ascending. |

**One semantic that is not self-evident** (mirror in SKILL.md + `--help`, per the outline precedent): theme ids are stable only **within one index generation** — re-indexing may regroup and renumber. Store URIs across sessions, never `T<id>`.

(Theme order = size order and "most-linked" = the selection rule are now stated by the labels themselves; they no longer need out-of-band documentation.)

## Worked example

```
THEMES  my-knowledge-base   214 docs · 187 grouped into 3 themes by wikilinks · 27 ungrouped

T1  93 docs (43%) · moderately interlinked
    top wikilink targets   [[ki]] in 40 docs · [[vault]] in 18 · [[SKILL]] in 11
    most-linked docs       ki design ........................ D   vault://abc-123/projects/ki-design.md
                           Write-verb altitude split ........ D   vault://abc-123/projects/verb-split.md
                           (+91 more docs)
    links into T2 via      ki design ........................ D   vault://abc-123/projects/ki-design.md

T2  52 docs (24%) · tightly interlinked
    top wikilink targets   [[GraphRAG]] in 31 docs · [[Lucene]] in 12 · [[chunking]] in 9
    most-linked docs       GraphRAG indexing cost ........... D   vault://abc-123/research/graphrag-cost.md
                           Lucene vs vectors ................ D   vault://abc-123/research/lucene-vs-vectors.md
                           (+50 more docs)

T3  42 docs (20%) · loosely interlinked
    top wikilink targets   [[Aura]] in 22 docs · [[Podman]] in 14 · [[neo4j-cli]] in 8
    most-linked docs       Local Neo4j setup ................ D   vault://abc-123/ops/neo4j-local.md
                           Aura tier matrix ................. D   vault://abc-123/ops/aura-tiers.md
                           (+40 more docs)
```

## What's intentionally not in the output

- **Theme names/labels.** ki would have to fake them from term frequency; a bad label anchors the agent worse than no label. The wikilink-target line plus doc titles is the naming evidence; the agent does the naming.
- **Content snippets.** Blow the token budget the command exists to protect; the agent `ki get`s selectively. (A future `--preview` may add one line per doc; off by default.)
- **Scores.** Modularity, centrality, density floats — backend concepts. The cohesion word + counts carry everything actionable.
- **The full member list.** Default shows the most-linked rows + `(+N more docs)`; `--per-theme N` widens, future `--members T<id>` dumps all.
- **A coverage percentage in the header.** `187 grouped / 27 ungrouped / 214 total` already reconcile; a redundant % is one more number to misread.
- **`lastSeenAt` / timestamps** — same rationale as outline-format.

## Wire record format (producer → renderer)

Producer-agnostic contract; whatever computes clusters emits these rows, the renderer owns all formatting.

| Field | Type | Notes |
|-------|------|-------|
| `cluster_key` | opaque string | Producer's cluster identity. Renderer assigns `T<id>` by doc-count sort — `cluster_key` never renders. |
| `kind` | `"member"` \| `"link_target"` \| `"crossover"` | Row type. |
| `uri` | string | member: the doc. link_target: the wikilink-target node. crossover: the crossover doc. Full URI, verbatim. |
| `displayName` | string | Rendered name / `[[…]]` text. |
| `count` | int \| null | link_target: number of theme docs linking to it. member: within-theme link count (drives most-linked order). crossover: null. |
| `other_cluster_key` | string \| null | crossover: the cluster on the far side. Others: null. |
| `cohesion` | `"tight"` \| `"moderate"` \| `"loose"` | On member rows (constant per cluster). Producer computes; renderer renders the phrase. |

Header numbers derive from member rows plus one `total_docs` scalar from the producer; method phrase (`by wikilinks` / `by folders`) from one `method` scalar.

## Open questions

- Section-level themes (`S` rows) — deferred; format accommodates.
- `--under <folder>` scoping (#36) — header would read `THEMES <folder-uri> …`.
- Drill-down pairing with `ki outline <uri> --on links-to` (#46) — doc and link-target URIs must be valid roots for it.
