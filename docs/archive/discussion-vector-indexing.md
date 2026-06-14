# Discussion: vector indexing for `ki`

> **Status: deliberation, not spec.** This doc weighs five options for adding
> vector retrieval to `ki`. It does *not* lock in a decision. Once we pick a
> direction, the chosen option (or hybrid) gets promoted into
> `docs/requirements.md`, `docs/data-model.md`, `docs/retrieval-queries.md`,
> and `skills/ki/SKILL.md` as the canonical design.

## Why this is hard

v1 of `ki` deliberately ships with **no vector index** — fulltext over
`displayName + content + aliases` is the retrieval substrate, and `AGENTS.md`
explicitly says "don't add vector indexes in v1." The `genai` plugin loads
inside `neo4j-local` for the upgrade path, but it's unused today. This doc is
about what "post-v1" means.

Embeddings drag in concerns fulltext didn't:

- **A model identity.** A vector is meaningless without knowing what model
  produced it. Neo4j stores the floats but not the provenance.
- **A second credential surface.** Most quality embedders are paid SaaS APIs,
  which means yet another key for the user to wire up.
- **Query-time symmetry.** The query must be embedded with the *same* model
  used at ingest, or cosine similarity is gibberish.
- **Re-index cost.** Changing models means re-embedding every document. A 10k
  vault is cheap in tokens but slow in wall-clock and may be billable.
- **Privacy boundary.** Sending vault content to a cloud provider is a
  category change from "data lives on your machine in `~/.config/ki/`."

So this isn't only "which option is technically cleanest" — it's "which option
keeps `ki` honest to its design principles while opening the retrieval upside."

## Constraints any option must satisfy

Lifted from `docs/requirements.md` *Core design principle* and `AGENTS.md`
*Non-negotiable design principles*. If an option violates these, it's out —
not "with caveats," just out.

1. **`ki` is an index, not a document store.** No mutating source files. (None
   of the five options touch source files, but a naive "cache embeddings on
   disk next to the source" would; rule it out.)
2. **The backend is opaque to the user.** Users shouldn't need to know about
   Neo4j, Cypher, or vector indexes. Whatever we ship, `ki search` stays a
   single command with one obvious behavior.
3. **One source of truth per concern.** Embedding provider config goes in
   `~/.config/ki/config.yaml`, scoped to a profile (same place as the Neo4j
   connection). No parallel state, no per-vault config.
4. **Safe by default, dangerous by flag.** Spending money on third-party
   embedding APIs is account-touching. Opt-in, never auto-on, even in agent
   auto-mode. Same partition as "auto-mode never provisions Aura."

## The five options

| # | Where embeddings happen           | Cost owner    | SOTA models? | Local-only?                              | `ki` operational burden |
|---|-----------------------------------|---------------|--------------|------------------------------------------|-------------------------|
| 1 | Neo4j GenAI plugin (server-side)  | End user      | Yes          | Yes (with `neo4j-local` + local embedder) | Low                     |
| 2 | `ki`-hosted embedding service     | `ki` operator | Yes          | No                                       | **Very high**           |
| 3 | `ki` client-side (Python)         | End user      | Yes          | Yes (with local embedder)                | Medium                  |
| 4 | Bundled small embedder            | None          | No           | Yes                                      | High (model in package) |
| 5 | No text embeddings (graph-only)   | None          | N/A          | Yes                                      | Low                     |

### Option 1 — Neo4j GenAI plugin handles embedding

**Shape.** User configures an embedding provider in their `ki` profile
(`openai` / `voyage` / `cohere` / `azure-openai` / etc., plus credentials).
At ingest, `ki` builds `UNWIND $rows AS row` calls that invoke
`genai.vector.encodeBatch(...)` server-side; the resulting vectors land on
`Section.embedding` (and/or `Document.embedding`) and feed a Neo4j vector
index. At search time, `db.index.vector.queryNodes` is the substrate; the
GenAI plugin embeds the query with the same provider/model.

The GenAI plugin is available across **`neo4j-local`, Community Edition,
and every Aura tier** — same call surface in all three. `neo4j-local`
already loads it (per `AGENTS.md` it's loaded "for the upgrade path but
unused"), and Aura ships it automatically on every instance, so there's
full deployment parity. No tier-gating, no plugin-availability check to
write into `ki configure`.

We persist provider metadata as an `:IndexMeta` (or `:VectorIndex`) node tied
to the index name, with `provider`, `model`, `dimensions`, `metric`,
`createdBy` (User), `createdAt`. This is the load-bearing addition: Neo4j's
index catalog doesn't track which provider produced the vectors, so any
other user landing on the same Aura needs that metadata to know whether
they *can* query the index (do they have a key for that provider?).

**Pros**
- Embedding happens where the data already lives. No round-trip of
  document bodies over the user's wire — Neo4j calls the provider directly.
- Memory pressure stays on Neo4j, not on the `ki` Python process. Aligns
  with our "process one document at a time" Python-side discipline.
- Batch encode is a built-in plugin call; we don't reimplement retries,
  rate limiting, or token accounting.
- Query-time embedding uses the same plugin, so symmetry between ingest
  and query is automatic.
- Multi-user / shared-vault story is natural: any user with the corresponding
  provider key can query the existing index. Without a key, they fall back
  to fulltext (B.1 / B.2 already do this).

**Cons**
- Provider parity is whatever the GenAI plugin supports. The plugin's
  built-in providers are a fixed list (OpenAI, Vertex AI, Bedrock, Azure
  OpenAI, etc.); a provider it doesn't ship support for needs us to wait
  on Neo4j, write our own resolver, or fall back to Option 3 for that
  case.
- Doesn't help users who want **local** embedders (Ollama, llama.cpp, a
  local `nomic-embed-text`) on **Aura**. The plugin's custom-endpoint
  hook would have to reach the user's localhost, which Aura can't. On
  `neo4j-local` and self-hosted Community, the plugin *can* call
  `http://localhost:11434/...` directly (`neo4j-local` is a native
  install, not Docker), so the local-embedder case works there — it's
  only the cloud-Neo4j-plus-local-embedder combo that's a non-starter.
- Credentials still live in `~/.config/ki/config.yaml`; `ki` passes them
  to the plugin per call (`{token: $api_key, model: $model}` in the
  procedure config map), so there's no Neo4j-side restart or env-var
  configuration. That's simpler than it sounds, but it does mean every
  ingest Cypher batch carries the credential — fine for a single-user
  driver session, worth a note if we ever expose a shared Neo4j to
  multiple `ki` users with distinct provider keys.

**Verdict.** The cleanest fit for the cloud-provider case, and — given the
plugin works on `neo4j-local` and Community Edition — *also* a clean fit
for the local-Neo4j case. The one combination it can't serve is **Aura +
local embedder**, since Aura can't reach the user's localhost. Whether
that combination matters depends on whether "privacy mode" users would
ever pair their local embedder with a cloud Neo4j; in practice, a user
who cares enough to run a local embedder probably also runs local Neo4j.

### Option 2 — `ki` offers a hosted embedding service

**Shape.** `ki` (or rather, *we* — whoever ships `ki`) runs a paid SaaS
endpoint that embeds text. Users pay us; we abstract the model choice; the
client sends section bodies over the wire to our endpoint and writes the
returned vectors to Neo4j.

**Pros**
- One credential — `ki configure` collects a `ki` API key and that's it.
- We control the model choice and can upgrade everyone at once (with the
  caveat that "upgrade everyone" means "re-embed everyone's data," which is
  expensive and slow).
- Easiest user story by far: "just turn it on."

**Cons**
- We are now an **AI infrastructure business**, not an open-source CLI.
  This is the biggest single Con and it's category-changing. We'd need
  billing, a quota system, a status page, abuse protection, a privacy
  policy, SOC2 conversations, retention answers, model deprecation comms.
  None of this is the project we set out to build.
- Conflicts with the **opaque backend** principle in a worse way than the
  other options: we'd be inserting *ourselves* between the user and their
  retrieval. That makes `ki` an active service, not a tool.
- Conflicts with the implicit "your data lives where you put it" promise
  that makes `ki` safe to point at an Obsidian vault, a research folder,
  or anything sensitive.
- Vendor-locked: if our hosted endpoint goes down, every user's `ki search`
  breaks until we recover.
- Provides zero benefit users can't get themselves by paying OpenAI / Voyage
  / Cohere directly — we'd just be marking up someone else's API.

**Verdict.** Out. The wrong shape of project. Even if it were technically
straightforward, the principles say no.

### Option 3 — `ki` embeds client-side, ingests vectors

**Shape.** `ki index` runs the embedder *in the Python process*: load
section text, call a provider SDK (`openai`, `voyageai`, `cohere`,
`sentence-transformers`, `ollama`, `llama-cpp-python`, etc.) to get vectors,
write them to Neo4j as plain float arrays in the same `UNWIND` batch.
Provider config lives in `~/.config/ki/config.yaml`. `:IndexMeta` node still
tracks model identity.

**Pros**
- **Single credential surface** — `~/.config/ki/config.yaml`. No Neo4j
  config changes, no plugin to enable.
- **Works for both cloud and local embedders.** Same code path: an
  embedding provider is just "something with an `.embed(texts) -> floats`
  contract." `ollama` and `openai` are interchangeable from `ki`'s
  perspective. This is the **privacy-mode** win.
- **Works against any Neo4j** — no GenAI plugin required. Self-hosted,
  Aura-Free, Aura-Pro, all the same to us.
- We control retry / backoff / batching, which means we can be smarter
  about cost than the plugin (e.g., skip embedding for unchanged files via
  the same `Document.fileHash` mechanism we already use).

**Cons**
- Vault content travels client-side → provider → client → Neo4j. For cloud
  embedders that's one extra hop versus Option 1, but the data is going
  over the wire either way. The real cost is on the *user's* machine: a
  10k-section vault means ~10k provider API calls (or batched, ~100
  calls of 100 sections each), and the Python process has to hold the
  in-flight batch in memory. Bounded by our existing batch-size lever.
- Query-time symmetry is on us: `ki search` has to call the same embedder
  with the same model. Doable, but more code than "let the plugin do it."
- For very large vaults, client-side embedding is slower than letting
  Neo4j-side hardware do it — but in practice both are bottlenecked by the
  embedding provider's rate limits, not by who's holding the socket.
- Local embedders (sentence-transformers, ollama) add a heavy optional
  dep tree. We'd want to gate them behind `pip extras` (`ki[local-embed]`)
  rather than make `sentence-transformers` a hard dep.

**Verdict.** Strong fit for the **local-embedder / privacy** path, and
fine-but-second-choice for cloud providers. Most flexible. More client-side
code than Option 1.

### Option 4 — Bundled small embedder

**Shape.** `ki` ships with a fixed lightweight embedder in the wheel — e.g.,
a quantized `all-MiniLM-L6-v2` or `bge-small`. Embedding "just works" with
zero config, no API keys, no external services.

**Pros**
- Zero config. Truly. The user doesn't even need an account anywhere.
- No external dependencies, no rate limits, no network egress.
- Predictable: same model for every user, every vault.

**Cons**
- We are now shipping a **machine learning model inside a CLI tool**.
  Wheel size jumps from kilobytes to ~100MB+ (or we add a first-run
  download step, which is the worst of both worlds). `uv tool install
  knowledge-index` stops being a ~10-second operation.
- Locks every user into a 2022-era embedder. Quality is meaningfully
  worse than `text-embedding-3-large` or `voyage-3` for most retrieval
  tasks. We'd be selling a worse retrieval experience as the default.
- Hard to upgrade: every release that bumps the bundled model is an
  implicit "re-index everything." Users who don't re-index get silently
  degraded results.
- We inherit inference dependencies (`torch` or `onnxruntime` or
  `sentence-transformers`) as hard deps, which conflict with the "boring
  Python CLI" install story. `torch` alone is several hundred MB and a
  notoriously fussy install on weird platforms.
- Doesn't solve the privacy story any better than Option 3 with a local
  embedder, which can pick *any* local model — including a better one.

**Verdict.** Out, unless we discover users overwhelmingly want zero-config
embeddings *and* we find an inference dep that doesn't blow up the install
story. If we want this UX, Option 3 with a documented "use ollama with
`nomic-embed-text`" recipe gets ~80% of the benefit without bundling.

### Option 5 — No text embeddings; lean on graph signals

**Shape.** Stay on fulltext as the retrieval substrate, and add upgrades
that *don't* require any new credential or external service:

- **Wikilink display-text → target aliases (ingest-side).** When the
  parser sees `[[Darth Vader|Anakin]]`, append the display text
  (`"Anakin"`) to the *target's* `aliases` list — extending the same v1
  mechanism that already handles frontmatter aliases. The existing
  `content_search` fulltext index already covers `aliases`, so
  queries like "Anakin" match the target document with **zero retrieval-
  query changes**. Free upgrade for Obsidian / wikilink-heavy vaults;
  zero help for plain-markdown vaults. Small schema addition needed:
  give `Section` an `aliases` field for parity with `Document`, since
  wikilinks can target a section (`[[Darth Vader#origins|Anakin]]`).
  Display-text normalization (dedupe case-insensitively, drop a small
  stopword list — `"him"`, `"this"`, `"here"`, etc. — drop strings under
  a length threshold) keeps junk out of the alias list.
- **Agent-side query expansion (skill-side, no `ki` change).** The agent
  calling `ki search` is itself an LLM. For cultural-knowledge mappings
  ("Anakin" → "Darth Vader", "JFK" → "John F Kennedy"), the agent already
  knows the expansion and can rewrite/retry the query. Document this in
  `skills/ki/SKILL.md` as the recommended pattern: try the literal query;
  if results look weak, expand semantically and re-issue.
- **Existing v1 fulltext + frontmatter aliases.** Unchanged. The substrate.

Two extensions sit naturally on top but are *out of scope for the v2 cut*
under this option:

- **5a → 5b: LLM entity extraction at ingest.** Run an LLM over each
  section, extract entities + aliases, write them as `Document.aliases` or
  as `:Entity` nodes. Catches *personal-vault* aliases the calling agent
  can't possibly know — "Project Bluebird (BB)" yields `BB → Project
  Bluebird`. Comparable token cost to embedding everything. Same
  credential tax as the embedding options, which is why it's deferred
  together with them.
- **Centrality-based ranking (PageRank or in-degree).** See the *Direction*
  section below for why this is *also* deferred under this option, despite
  being one of the original motivations for picking the graph-signal path.

**Pros**
- **Zero new infrastructure, zero new credentials.** Aligns hardest with
  the project's existing principles.
- **Wikilink display-text is a small, contained change** — one parser
  enhancement, one edge-property addition to `docs/data-model.md`, one
  small adjustment to `B.1` / `B.2` to also match on the new property.
  Days of work, not weeks.
- **Agent-side expansion costs `ki` nothing** — it's a skill-doc edit. The
  LLM is already in the loop (the agent calling `ki search` is one); we
  just tell it to use itself.
- Graph-native retrieval has real legs: B.3 (neighbourhood), B.9
  (backlinks), and B.10 (shortest path) already exploit `LINKS_TO`. The
  wikilink-display-text upgrade extends that surface meaningfully.
- We may discover that for *personal vaults* — where the user already
  organized the data into folders, named files meaningfully, and added
  wikilinks — graph signals plus fulltext plus agent-side rewriting are
  good enough that vectors buy less than expected.

**Cons**
- **Unknown ceiling.** Fulltext + graph signals + agent-side expansion
  cap out somewhere; we don't know where. Embeddings demonstrably help
  for "find me the note about the thing I'm thinking of but can't name."
  Without them, that query class is weak.
- **Plain-markdown vaults (no wikilinks) get less of the upgrade.** The
  display-text trick is `LINKS_TO`-bound; a vault that's all plain prose
  with no `[[…]]` syntax gets nothing from it.
- **Agent-side expansion is bounded by what the calling LLM knows.** It
  handles "Anakin → Darth Vader" because the model has Star Wars
  knowledge. It cannot handle "BB → Project Bluebird" unless the
  user-written prose somewhere explicitly establishes the mapping. (And
  if it does, the LLM can read that section and figure it out — but only
  if the section is in its context, which is a chicken-and-egg with
  retrieval.) For *personal* aliases, this approach reaches its limit
  fast; that's the 5b case.
- **`ki search` from non-LLM contexts** (a shell script, a cron job)
  doesn't get agent-side expansion. Those callers fall back to plain
  fulltext.
- Means the `genai` plugin loaded in `neo4j-local` stays unused for now.

**Verdict.** **Chosen direction for v2 (the 5a flavor — see *Direction*
below).** Not a "we'll never do embeddings" decision; it's "we ship the
cheapest, no-credential upgrades first, watch the failure modes, and
revisit embeddings (or 5b) when the gap is concrete." The question comes
back the first time we see a real query that fulltext + wikilink expansion
+ agent-side rewriting can't reach.

## Cross-cutting concerns

These bite regardless of which option we pick.

### Provider/model metadata is load-bearing

Neo4j stores `embedding` as a float array. It does not store which model
produced it. Any option that ships embeddings (1, 2, 3, 4) needs an
`:IndexMeta` (or similar) node, MERGE-keyed on the vector-index name,
carrying:

- `provider` — `openai` / `voyage` / `cohere` / `ollama` / `local`
- `model` — `text-embedding-3-large` / `voyage-3` / `nomic-embed-text` /...
- `dimensions` — must match the index definition
- `metric` — `cosine` / `euclidean`
- `createdBy` — `User.id` (link to `:User`)
- `createdAt`, `lastWrittenAt`
- `vectorIndexName` — the actual Neo4j index name (e.g.
  `section_embedding_openai_3_large`)

This unlocks:

- **Multi-index coexistence.** User A's OpenAI index and User B's Voyage
  index can live in the same DB on the same vault. `ki search` picks the
  index the current profile can authenticate against.
- **Drift detection.** If `IndexMeta` says `dimensions=3072` but the index
  was somehow created at 1536, fail loudly at search time.
- **Cost control.** "Do not embed; this section is already in
  `IndexMeta.lastWrittenAt > Document.lastModifiedAt`" is the same idea as
  `Document.fileHash`, applied at the embedding layer.

### Query-time symmetry is non-negotiable

A query embedded with `voyage-3` against an index built with
`text-embedding-3-large` returns nonsense. `ki search` needs to read
`IndexMeta` for whatever index it's about to hit and embed the query with
*that* model. Option 1 gets this for free via the plugin; Option 3 needs
explicit handling.

### Which index does `ki search` hit?

If multiple `:IndexMeta` indexes exist for the same vault:
- Profile-bound: each `~/.config/ki/config.yaml` profile knows which provider
  it can authenticate with; pick the index that matches.
- No-match fallback: drop to the existing fulltext `B.1` / `B.2`.
- Explicit override: `ki search --index <name>` for the power user.

This means we never have to make a "global" choice across all users on a
shared vault. Each user's profile picks the index they can afford to query.

### Re-indexing when the model changes

`Document.fileHash` already skips unchanged files at ingest time. We can
extend the same idea to embeddings: per `(document_uri, model_id)` we
track "what hash was embedded last time," and only re-embed when either
the file changes *or* the model changes. This is the cheapest re-index
behavior; it makes "I bumped to a better embedder" a tractable migration
instead of "re-embed everything tonight."

### Local-vs-cloud is the real axis

Options 1 / 3 differ less on technology and more on *who pays compute*. A
user who already has an OpenAI key and an Aura instance with the GenAI
plugin enabled is best served by Option 1. A user on `neo4j-local` who
wants to point Ollama at their vault is best served by Option 3. We may
genuinely need both code paths, behind a common surface.

## Direction

**Ship Option 5a — graph signals + agent-side query expansion. Defer
embeddings (Options 1 / 3) and centrality-based ranking until we have
usage data.**

Concretely, the v2 cut adds:

1. **Wikilink display-text → target aliases.** Parser appends
   `[[Target|Display]]` display text (normalized: lowercase-dedup,
   stopword-filtered, length-thresholded) to the *target's* `aliases`
   list. Existing `content_search` fulltext already indexes
   `aliases`, so **no retrieval-query changes** — the new alias terms
   just start matching. Schema change: add an `aliases` field to
   `Section` for parity with `Document` (wikilinks can target sections,
   not just documents), and extend the fulltext index to cover
   `Section.aliases`. Self-contained, ~days of work.

2. **Agent-side query expansion as a documented skill pattern.** Update
   `skills/ki/SKILL.md` so the calling agent's recommended behavior is:
   try the literal query, inspect results, expand semantically if results
   look weak, retry. No `ki` code change; this is purely a skill-doc edit.
   Free upgrade for any agent that follows the skill.

3. **Existing v1 fulltext + frontmatter aliases.** No change; this is the
   substrate.

That's the whole shipping list. Everything else is deferred.

### What's explicitly deferred (and why)

**PageRank.** Genuinely useful — a global centrality score over `LINKS_TO`
would directly improve `B.1` / `B.2` / `B.3` ranking. **And** PageRank is
fundamentally a global-graph algorithm: it has to see the whole link
structure to assign meaningful scores, so it belongs server-side in the
database, computed once per ingest. **GDS is the right tool for it** —
not a Python NetworkX reimplementation. Computing PageRank client-side
would dodge the deployment-tier issue but moves a graph algorithm out of
the graph database, which is the wrong shape for this project.

The deployment-tier issue is the real blocker: GDS Community Edition runs
free on `neo4j-local` and self-hosted Community / Enterprise, but on Aura
it requires a **paid tier** — Pro / VDC / Business Critical — with only a
limited free trial on Pro. Shipping a feature that works on the most
accessible Aura tier (Free) only by *not running* would create an awkward
two-class experience right at the install step.

So: **PageRank is deferred until usage data tells us ranking quality is a
felt problem.** When that day comes, the right move is GDS server-side
and a documented "this feature requires `neo4j-local`, self-hosted, or
Aura Pro+." A free-trial path on Aura Pro covers the curious user without
us building a permanent fallback path.

**`graphRank` as a generic ranking-property abstraction** (with a cheap
in-degree-centrality stand-in now and a swap to PageRank later) was
considered. **Rejected** on premature-abstraction grounds: it adds a new
schema property, a new ranking lever in retrieval queries, and a future
migration story — all to ship a metric whose marginal value we haven't
measured. If we eventually want it, the schema-and-code cost is small
enough to add then. Better to keep the schema honest about what's
actually computed.

**Embeddings (Option 1, with Option 3 as escape hatch).** Still the
leading embedding path when we get there — same plugin works on
`neo4j-local`, Community, and every Aura tier; credentials pass at
call-site; server-side embedding keeps document bodies off the Python
wire. But the marginal complexity (provider credentials, `:IndexMeta`
schema, per-model re-index logic, query-time symmetry, multi-index
selection) only earns its keep if the 5a ceiling is too low. Until we
see real queries that 5a can't reach, we don't know whether we need the
complexity.

**Option 5b — LLM-driven entity extraction at ingest.** Conceptually
attractive (richer queryable graph instead of opaque vectors, comparable
token cost), but same "user wires an LLM credential" tax as embeddings.
If we eventually decide we need ingest-time LLM enrichment, this is the
direct alternative to embeddings to consider then. Until then: deferred
together with Options 1 and 3.

### What this Direction explicitly rejects

- **Option 2** as out-of-scope; we are not in the embedding-API business.
- **Option 4** as bad ROI; the install-size hit isn't worth a mediocre
  embedder when local-via-ollama (through the plugin's custom-endpoint
  hook) gets us the same UX with a better model and no `torch` dep.
- **Client-side Python PageRank** as a tier-dodge. Right answer when we
  ship centrality is GDS server-side; the deployment-tier limitation is
  honest and the free-trial path on Aura Pro covers the curious user.
  Reimplementing in NetworkX would be a workaround dressed up as a
  decision.

### Signals to revisit this Direction

Move embeddings and/or PageRank off the deferred pile when we see:

- Concrete queries (from real users or real agent transcripts) that 5a
  demonstrably can't reach. Particularly: queries for personal-vault
  aliases the calling agent has no way to know (codenames, internal
  nicknames, project IDs).
- Ranking complaints — multiple results returned, none obviously the
  right one — repeated across users. This is the PageRank case.
- A meaningful share of `ki search` traffic coming from non-LLM contexts
  (shell scripts, cron jobs) where agent-side expansion isn't available.
  This would push us toward an in-`ki` expansion mechanism, possibly
  embeddings.
- Vaults large or dense enough that link-density on `LINKS_TO` would make
  PageRank visibly useful (the open question below).

## Open questions

### Direct to the chosen Direction (5a)

- **Display-text normalization rules for the alias list.** The
  alias-the-target approach needs concrete rules to keep the alias list
  signal-rich: length threshold (3 chars? 4?), stopword list (`"him"`,
  `"this"`, `"here"`, …), case folding (preserve original *and* index
  lowercase, or just lowercase?), per-document cap on alias count, and
  what to do when frontmatter aliases conflict with derived ones. None
  of these are hard; they just need a one-time spec decision when 5a
  lands.
- **`Section.aliases` parity with `Document.aliases`.** Add it now (this
  change) so wikilink-to-section targets benefit from the same fulltext
  hit path. Confirm the fulltext index extension covers it. Belongs in
  `docs/data-model.md` and `docs/ingest-cypher.md`.
- **What does "results look weak" mean for the calling agent?** The
  skill-side expansion pattern relies on the agent deciding when to
  retry. A clear heuristic ("zero hits," "low top score," "no
  document-level match") makes the pattern reliable across agents
  without overspecifying.
- **`ki search` from non-LLM contexts.** Shell scripts and cron jobs lose
  agent-side expansion. Do we ship a `--expand` flag that opts into a
  configured-LLM expansion as a future option, or accept that these
  callers get plain fulltext?

### Held against the deferred pile (revisit when 5a hits its ceiling)

- **Does PageRank on `LINKS_TO` *actually* help on personal vaults**, or
  is the link density too low? Worth measuring against the deterministic
  test vault from `scripts/gen_test_vault.py` *before* committing to the
  GDS dependency — measurement first, then decide whether the deployment-
  tier cost is worth it.
- **For Option 3** (whenever we get there): is `sentence-transformers` an
  acceptable optional dep, or do we go all-in on `ollama` as the local
  recommendation to dodge the `torch` install footprint?
- **Does `:IndexMeta` go in `docs/data-model.md`** even if 5a doesn't
  write it, as a forward declaration? Lean toward yes once embeddings
  move off the deferred pile; until then, no — premature.
- **How does `ki rm --vault` interact with `:IndexMeta` and vector
  indexes?** Removing the vault should drop the embeddings; whether it
  drops the index itself depends on whether other vaults share that
  index.
- **Embedding-provider key rotation.** When a user rotates their key, the
  old `IndexMeta.createdBy` profile still points at the old credential.
  Probably: re-embed under a new index name and leave the old
  `IndexMeta` for cleanup.
