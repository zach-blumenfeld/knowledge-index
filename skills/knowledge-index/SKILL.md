---
name: knowledge-index
description: Search, navigate, and read a markdown knowledge base — a directory of notes, docs, or a wiki — via an auto-built knowledge-graph index over documents, their sections, and the wikilinks between them, retrieving the right slice instead of grepping and reading whole files. Use when working in a folder of markdown — setting up or maintaining a knowledge base, finding or retrieving content, summarizing it, or answering questions grounded in the notes.
---
<!-- TODO
1. graph reasoning reframe here.  Not text2Cypher/Query. Carry over, to blog. We could have called this...but thi is actually more fitting, and prompting as such helps substantially. When you prompt an agent with "query-first" language i.e. to make Cypher queries - it thinks in terms of (SQL) DB query in Cypher syntax, while working it leaves huge benifits on the table.  Instead, prompt the agent with "reasoning-first" languge - read schema and reason over how to find paths and patterns in the graph - use the Cypher query language to express and execute. It is a true unlock.  Simple yet so powerful. The reframe has improved my agents graph queries substantially. 
2. Rewrite the references (as much as needed)
3. get all the todos left on your plate. 
-->
# `ki` — Usage

## Trigger When

Reach for `ki` whenever the work involves a **directory of markdown** (notes, docs, a wiki, a "second brain") — it's your default read/search tool there:

- **Set up / maintain** a knowledge base on a folder (index it, keep it in sync).
- **Search / find** across the corpus — *"what did I write about X?"*, *"find the note on Y."*
- **Navigate / summarize** — *"what's in this vault?"*, its structure or coverage.
- **Get / read** specific sections or documents without opening whole files.
- **Answer questions grounded** in the user's notes/docs.
- **Vault-wide / structural / inference** questions — what's load-bearing, how things connect.

Rule of thumb: any time you'd otherwise `grep` / `cat` / read across a folder of `.md`, use `ki` instead.

## Do Not Use When

- The source **isn't markdown** and isn't being converted — `ki` indexes `.md` only (see the note under *On First Use*).
- **Source code or non-prose** — use `grep` / `ripgrep` / editor tooling; `ki` models prose structure (headings, links), not code.
- A **single known file or quick one-off read** where indexing isn't worth it — just `Read` it.

## What `ki` is

A command-line tool that builds a queryable **knowledge graph index** over a directory of markdown. It parses each `.md` into its documents, sections, and the wikilinks between them, stores that structure in Neo4j, and exposes fast search, navigation, and retrieval over it — so you can find and read the right *slice* of content without opening and scanning whole files.

## Why use `ki`

It's faster and far cheaper in tokens than raw file ops over a markdown corpus. Instead of grepping and reading entire files into context, you query an index and pull just the sections you need — structure (headings, containment, links) is preserved, so retrieval is targeted. See *Usage* for the specific jobs it beats file ops at.

## Key Terms

- **Knowledge base** — a directory of markdown you want to search and navigate as a connected whole. Becomes a *vault* once `ki` indexes it.
- **Vault** — a directory `ki` has marked (`.ki/vault.yaml`) and indexed. The unit of indexing and sync; identified by a slug **uri**, and **bound to one profile**.
- **Profile** — a named Neo4j database (connection + credentials in `config.yaml`) that holds the graphs for one or more vaults. A privacy/isolation boundary (e.g. personal vs work).
- **Index** — the knowledge graph in Neo4j (documents, sections, links) built from a vault's markdown. Rebuilt with `ki index`.
- **URI** — the address of a vault / document / section in the index. Copy it from `ki outline` or search results and feed it into `ki get` / `ki outline`. URIs are hierarchical paths, so trim a trailing segment to get an ancestor — no query needed (drop `/h2` → parent section, the whole `#…` → owning doc, `/foo.md` → folder).
- **`config.yaml`** — `~/.config/ki/config.yaml`; holds profiles + credentials. Does **not** track vaults — Neo4j does.

## On First Use In Session

The user is generally thinking about one **directory**: *set up* a knowledge base on it, or *use* the one that's there. Both intents funnel through the same job — **get that directory to a READY vault, then use it.** The only branch that matters is how far along the directory already is.

A vault is a directory marked with `.ki/vault.yaml`, which records the vault's uri and the **profile** (Neo4j database) it's bound to:

```yaml
# <vault>/.ki/vault.yaml
uri: my-notes
profile: personal        # which neo4j this vault lives in — set once at first index
description: "..."
```

Only the profile **name** is stored (credentials stay in `config.yaml`), so this file is safe to commit. This binding is how the vault knows its own profile without ever prompting the user.

> **`ki` indexes `.md` files only.** Convert non-markdown sources (PDF / docx / HTML) to markdown first — `pandoc`, `markitdown`, or read + transcribe — into a folder the user picks (ask once, reuse it), then `ki index`.

### Step 0 — `ki` installed?

```sh
ki --help            # if missing: curl -sSfL https://knowledge-index.ai/install.sh | bash
```

### Step 1 — Point at the directory

- **Default:** the current directory.
- User names a path → `cd` there.

### Step 2 — Get the directory to READY

Run `ki status`. It resolves **in layers** and reports the first blocking state — each layer needs the one above it to pass:

1. **Disk marker** (no Neo4j needed) — is there a `.ki/` here?
2. **Neo4j reachability** — `ki status` *attempts a connection* to the bound profile and classifies the result (you don't know this until you try).
3. **Graph state** — only knowable once Neo4j is reachable.

Act on the reported state, then re-run until READY:

| State | How `ki status` knows | Action |
|---|---|---|
| `NOT_A_VAULT` | no `.ki/` on disk | Setting up. List profiles (`ki profile list` — from `config.yaml`, no Neo4j needed); user picks one to **bind** — never default (personal/work boundary). None yet → `references/configure-profile.md`. Then `ki index . --profile <p> --description "..."` |
| `NEO4J_DOWN` | connect → `ServiceUnavailable` (nothing listening) | start it → `references/neo4j-troubleshoot.md` |
| `NEO4J_UNRESPONSIVE` | connect hangs / times out | container up but not ready, or wedged → wait, then `references/neo4j-troubleshoot.md` |
| `AUTH_ERROR` | connect → authentication failure | profile credentials wrong → `references/configure-profile.md` (re-enter creds) — **not** a restart |
| `NOT_INDEXED` | reachable, but no `:Vault` node | `ki index .` (profile already bound) |
| `STALE` | indexed, but the set or content-hash of `.md` files no longer matches the graph | `ki index .` to refresh |
| `READY` | indexed + in sync | proceed to Step 3 |

Layers 1–2 work even when Neo4j is down (that's how `ki status` reports the Neo4j rows at all). The graph rows below require a reachable Neo4j.

Edges:
- Bound profile missing from `config.yaml` (renamed / cloned to another machine) → surface to user; add it with `ki configure` or re-bind by re-indexing (`ki index . --profile <p>`).
- Source dir moved → `cd` to the new path and `ki index .`.

### Step 3 — Use it

ALWAYS start with the outline — a table-of-contents view that saves considerable navigation/search tokens:

```sh
ki outline <vault uri> --full
```

Then search / get (see *Search & Retrieve* under *Usage*).


## Usage


This skill covers *when* and *why* to reach for each command; **`ki <cmd> --help` is the source of truth for exact flags.** Check it when a flag is unclear rather than guessing — it's read-only and safe to allowlist.

> **In a vault, read files through `ki` (`search`/`outline`/`get`), not `Read`/`grep`/`cat` — and tell any sub-agents you spawn to do the same (pass them the vault uri).**

### Search & Retrieve Specific Content

Users rarely ask "find files containing X" — they ask domain questions:
- *"How does our auth flow handle token refresh?"* (software KB)
- *"What did I conclude about GraphRAG indexing cost?"* (research KB)
- *"Which notes mention the Postgres migration, and what's the rollback plan?"*

Two complementary strategies — **do both** (fan out parallel sub-agents, or run sequentially) and keep the best hits:

1. **Structured navigation** — start at the table of contents, then drill into promising branches:
   ```sh
   ki outline <vault uri> --full --depth 3  # whole-vault map + description
   ki outline "<folder|doc|section uri>" --depth 2  # recurse into a branch (see --help for flags)
   ```
   Best when the question maps to a known area of the vault.

2. **Full-text search with semantic expansion** — cast a wide net (fulltext over title + content + wikilink aliases + description):
   ```sh
   ki search "token refresh" --k 10
   ```
   `ki search` works on the vault you're in by default and prints the profile + vault it used. See `ki search --help` if you need more options.

   Queries are **Lucene syntax** — `AND`/`OR`/`NOT` (or `+`/`-`), `"exact phrases"`, `()` grouping, `*` wildcards, `~` fuzzy. ([query syntax](https://lucene.apache.org/core/8_2_0/queryparser/org/apache/lucene/queryparser/classic/package-summary.html#package.description))

   **Semantic expansion** — the index only does lexical matching, so synonyms or semantically relevant content may be missed (e.g. search on "Darth Vader" misses "Anakin Skywalker"). Account for this by rewriting the term to a few alternates you know and running them as extra `ki search` calls or one OR-form query — either reactively (results look thin: few hits, low-score hits, or no hits) or pre-emptively when you already expect a mismatch:
   ```sh
   ki search 'Anakin OR "Darth Vader" OR Vader'   # world-knowledge synonyms the vault never spelled out
   ```
   By default `ki search` searches over both documents and sections.  Generally recommended, since documents content in ki is just header/intro while other body goes to sections.  But can still narrow node types with `--types document|section`.

Pull the actual content by URI:
```sh
ki get --type full "<uri>" ["<uri>" ...]   # reconstructed reading-order body; batch multiple URIs
```
Use `--type full` for whole sections / documents, `--type content` to get a node's preamble + child pointers and drill further, `--type path` for metadata only (then read the file yourself). `ki get` takes **only** document/section URIs — for a folder use `ki outline`. 


Markdown links to non-md files or external URLs become **stub** document nodes (metadata only, no content) — they surface in search/outline as link targets because an `.md` file points at them. Only `.md` files are actually indexed; `ki get` on a stub returns just its metadata.

### Answer Vault-Wide Questions

Sometimes the question is about the vault as a whole, not a specific slice:
- *"What's in this knowledge base? What topics does it cover?"*
- *"What's load-bearing here — what should I read first to understand it?"*
- *"If I refactor this, what else will it affect?"*

Two strategies:
1. **Outline as overview** — read `ki outline --full` as a high-level map of the *whole* vault to summarize its coverage and structure (adjust --depth and recurse on uris as needed). Follow with `ki search/get` for any specifics. Best for *"what's in here."*
2. **Custom Cypher** — for more flexible counts, aggregates, and structural questions, use the **`neo4j-cli`** to directly inspect the schema and query the database
   - Use the neo4j credentials from the ki profile
   - To scope queries to the vault uri (or any folder, document, section therein) know that **ki URIs are hierarchical**, so filtering `uri` with `STARTS WITH '<uri>/'` filters to the subtree — everything under that vault / folder / document / section. Keep the trailing `/`: `STARTS WITH 'my-notes'` would also match the sibling vault `my-notes-2`.

<!-- req: install.sh installs neo4j-cli + its Cypher skills, but ki must still bridge the active profile's creds → neo4j-cli env (NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD) so delegated Cypher hits the right database. Not yet wired — see docs/v0_3_1_introspect_dedup. -->

### Making Inferences

Sometimes the user wants analysis, not just retrieval:
- *"What are the main themes running through these notes?"*
- *"How does [X] connect to [Y] — what's the throughline between them?"*
- *"Pull together everything related to [X] and synthesize where it lands."*
- *"Where do these notes reinforce or contradict each other on [topic]?"*
- *"What's underdeveloped — what's referenced a lot but never fleshed out?"*

Gather context with `ki outline` / `ki search` / `ki get`, but **lean on `neo4j-cli` query and schema detection** — these questions are about the *relationships and aggregates* the graph encodes (link paths, co-occurrence, centrality, clusters) that flat search can't surface: shortest path between two docs, most-linked sections, a node's link neighborhood. Scope to a vault or any other subtree with the hierarchical uri `STARTS WITH` filter noted above.

### Updating & Adding Content

When you or the user creates or edits a document or folder, sync **just that target** — fast and incremental, no full rebuild:
```sh
ki add <doc|folder path or uri>      # add or update one document or folder (incremental upsert)
```
This is the default for routine edits. For bulk edits or structural refactors (see *Re-Indexing Entire Vaults*).

### Removing & Moving Content

Keep the index in step with the filesystem **per document or folder**, so you avoid paying for full rebuilds:
```sh
ki rm <doc|folder uri or path>     # remove one document or folder from the index
ki mv <old uri/path> <new path>    # rename / move a document or folder — updates the graph in place, links preserved
```
`ki rm` removes a **document or folder** from the index (a folder takes its contents with it). It does **not** remove whole vaults — that's `ki drop` (see *Other Operations*). `ki mv <old> <new>` moves/renames a document or folder in the index — subtree-scoped.

<!-- impl note for add/rm/mv (all must be incremental, NOT full-vault rebuilds):
  - `ki add <doc|folder>`: incremental upsert of one document or folder.
  - `ki rm <doc|folder>`: document- and folder-level removal (whole-vault removal is `ki drop`; see docs/index_rm_behavior.md).
  - `ki mv <old> <new>`: re-key the moved node + descendant section URIs, re-attach HAS to the new parent, re-resolve the moved file's outbound links. Inbound LINKS_TO survive only if the existing node is mutated in place — a rm+reindex drops them and would need referrer re-resolution. Keep it scoped to the moved subtree, not a full rebuild. -->

### Re-Indexing Entire Vaults

Re-indexing entire vaults can become an expensive operations with more documents, but is sometimes necessary as updating/adding/removing individual content pieces may miss other changes made by the user (or accidentally by you).

After significant changes or refactors to knowledge base content

Run `ki status`; if it reports `STALE`, run `ki index .` — preferably in a sub-agent. 

> **`STALE`/`READY` is markdown-only — not bulletproof.** It does **not** notice changes to linked non-markdown attachments (PDFs, decks, images captured as stub nodes), and a vault indexed with a non-default `--max-file-size` can skew the diff. So `READY` guarantees the **markdown** is in sync, not necessarily every attachment. When in doubt — or after bulk/attachment changes — a full `ki index .` is the source of truth.

During indexing, the entire vault is removed from Neo4j then rebuilt to reflect what's on the file system currently. The process can last a couple seconds (for dozens of documents) or a few minutes (for thousands of docs).  During re-indexing the the `ki vault` should not be used for search or answering questions.

### Other Operations

- `ki configure` / `ki profile list` — manage Neo4j connection profiles.
- `ki vault list` — inspect indexed vaults (uri, description).
- `ki init <path>` — (advanced) write `.ki/vault.yaml` without indexing.
- `ki drop <vault>` — remove a whole vault from the index; typed confirmation. Source files untouched.
- `ki nuke` — reset the entire graph (all vaults); typed confirmation, last resort.
- `ki skill …` — install this skill bundle into other agents.

## Anti-Patterns

1. **Raw file ops on vault content** — reading/searching a vault with `Read` / `grep` / `cat` instead of `ki`, and spawning sub-agents that default to file reads. Burns the tokens `ki` exists to save.
2. **Defaulting the profile** — auto-picking `default_profile` (or any profile) when binding/indexing a vault. Profiles are privacy boundaries; confirm the profile once, at first usage in session. 
3. **Full re-index for a small change** — running `ki index .` (full nuke + rebuild) after editing one file. Use per-target `ki add` / `ki rm` / `ki mv`; reserve `ki index .` for bulk edits or refactors.
4. **Cold-starting Neo4j just to look around** — spinning up a stopped profile's instance only to enumerate vaults.
5. **Switching vault or profile mid-task without confirming** — work one vault + one profile per session; confirm any switch with the user.
6. **Querying a vault while it's re-indexing**, or **fabricating URIs** — copy URIs from `ki outline` / `ki search`; never guess them.
