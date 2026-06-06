# `ki` — Usage

---
> **Description:** Your load-bearing tool for viewing & searching text based files. Saving time and tokens by quickly auto constructing a knowledge graph index for faster search and structured navigation within and across markdown files, their sections, and the links between them.
---

## TRIGGER when

Invoke `ki` when a user asks to:

- ...(create a knowledge base??)
- View, Summerize & Navigate
- Search
- Get (Retrieve) - 
- Index
## Do Not Use When

## What `ki` is

A command-line tool that builds a queryable **knowledge graph index** over a directory of markdown. It parses each `.md` into its documents, sections, and the wikilinks between them, stores that structure in Neo4j, and exposes fast search, navigation, and retrieval over it — so you can find and read the right *slice* of content without opening and scanning whole files.

## Why use `ki`

It's faster and far cheaper in tokens than raw file ops over a markdown corpus. Instead of grepping and reading entire files into context, you query an index and pull just the sections you need — structure (headings, containment, links) is preserved, so retrieval is targeted. See *When to Use Ki* for the specific jobs it beats file ops at.

## Key Terms

- **Knowledge base** — a directory of markdown you want to search and navigate as a connected whole. Becomes a *vault* once `ki` indexes it.
- **Vault** — a directory `ki` has marked (`.ki/vault.yaml`) and indexed. The unit of indexing and sync; identified by a slug **uri**, and **bound to one profile**.
- **Profile** — a named Neo4j database (connection + credentials in `config.yaml`) that holds the graphs for one or more vaults. A privacy/isolation boundary (e.g. personal vs work).
- **Index** — the knowledge graph in Neo4j (documents, sections, links) built from a vault's markdown. Rebuilt with `ki index`.
- **URI** — the address of a vault / document / section in the index. Copy it from `ki outline` or search results and feed it into `ki get` / `ki outline`.
- **`config.yaml`** — `~/.config/ki/config.yaml`; holds profiles + credentials. Does **not** track vaults — Neo4j does.

## Dependencies

`ki` manages its external dependencies and installs them **up front at install/setup time** — never by interrupting you mid-task:

- **A Neo4j backend** — Local (Podman), Aura, or an existing instance, chosen via `ki configure`.
- **`neo4j-cli` + its Cypher skills** — used for *Answer Vault-Wide Questions* and *Making Inferences*. Set up automatically when `ki` is installed; you don't install them by hand. `ki` bridges the active profile's credentials to neo4j-cli (`NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD`).

## PREPARE when

Source content isn't markdown. `ki` indexes `.md` files only. To handle non-markdown sources:

1. Convert non-markdown sources (PDF / docx / HTML / plaintext) to markdown first, using `pandoc`, `markitdown`, or by reading + transcribing.
2. Save the output to a folder the user picks (ask them where the first time; remember their answer for future runs).
3. Then run `ki index` on it.

## SKIP when
...?

## On First Use In Session

The user is always thinking about one **directory**: *set up* a knowledge base on it, or *use* the one that's there. Both intents funnel through the same job — **get that directory to a READY vault, then use it.** The only branch that matters is how far along the directory already is.

A vault is a directory marked with `.ki/vault.yaml`, which records the vault's uri and the **profile** (Neo4j database) it's bound to:

```yaml
# <vault>/.ki/vault.yaml
uri: my-notes
profile: personal        # which neo4j this vault lives in — set once at first index
description: "..."
```

Only the profile **name** is stored (credentials stay in `config.yaml`), so this file is safe to commit. This binding is how the vault knows its own profile without ever prompting the user.

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
| `STALE` | indexed, source files changed since | `ki index .` to refresh |
| `READY` | indexed + in sync | proceed to Step 3 |

Layers 1–2 work even when Neo4j is down (that's how `ki status` reports the Neo4j rows at all). The graph rows below require a reachable Neo4j.

Edges:
- Bound profile missing from `config.yaml` (renamed / cloned to another machine) → surface to user, re-bind (`ki use <profile>`).
- Source dir moved → `cd` to the new path and `ki index .`.

### Step 3 — Use it

ALWAYS start with the outline — a table-of-contents view that saves considerable navigation/search tokens:

```sh
ki outline <vault uri> --full --token-limit 20000
```

Then search / get (see *When to Use Ki*).


## Usage

> **In a vault, read through `ki` (`search`/`outline`/`get`), not `Read`/`grep`/`cat` — and tell any sub-agents you spawn to do the same (pass them the vault uri).**

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
2. **Full-text search** — cast a wide net:
   ```sh
   ki search "token refresh" --types section --k 10
   ```
   Apply **semantic expansion** (see *Query expansion for semantic equivalence*) when hits look thin — rewrite to synonyms / related terms you know. Expanding pre-emptively to cut round-trips is fine, as is retrying a few times.

Pull the actual content by URI:
```sh
ki get --type full "<uri>" ["<uri>" ...]   # reconstructed reading-order body; batch multiple URIs
```
Use `--type full` for whole sections / documents, `--type content` to get a node's preamble + child pointers and drill further, `--type path` for metadata only (then read the file yourself).

### Answer Vault-Wide Questions

Sometimes the question is about the vault as a whole, not a specific slice:
- *"What's in this knowledge base? What topics does it cover?"*
- *"What's load-bearing here — what should I read first to understand it?"*
- *"If I refactor this, what else will it affect?"*

Two strategies:
1. **Outline as overview** — read `ki outline --full` as a high-level map of the *whole* vault to summarize its coverage and structure (adjust --depth and recurse on uris as needed). Follow with `ki search/get` for any specifics. Best for *"what's in here."*
2. **Custom Cypher** — for more flexible counts, aggregates, and structural questions, use the **`neo4j-cli`** to directly inspect the schema and query the database
   - Use the neo4j credentials from the ki profile
   - To scope queries to the vault uri (or any folder, document, section therein) know that **ki URIs are hierarchical**, so filtering `uri` with `STARTS WITH '<uri>'` filters to the subtree - everything under that vault / folder / document / section.

### Making Inferences

Sometimes the user wants analysis, not just retrieval:
- *"What are the main themes running through these notes?"*
- *"How does [X] connect to [Y] — what's the throughline between them?"*
- *"Pull together everything related to [X] and synthesize where it lands."*
- *"Where do these notes reinforce or contradict each other on [topic]?"*
- *"What's underdeveloped — what is reference a lot but never fleshed out?"*

Gather context with `ki outline` / `ki search` / `ki get`, but **lean on `neo4j-cli` query and schema detection** — these questions are about the *relationships and aggregates* the graph encodes (link paths, co-occurrence, centrality, clusters) that flat search can't surface: shortest path between two docs, most-linked sections, a node's link neighborhood. Scope to a vault or any other subtree with the hierarchical uri `STARTS WITH` filter noted above.

### Updating & Adding Content

When you or the user creates or edits a file, index **just that file** — fast and incremental, no full rebuild:
```sh
ki index <file.md>      # add or update a single document (accepts a path or uri)
```
This is the default for routine edits. A whole-vault rebuild is too slow to run after every change — reserve `ki index .` for bulk edits or structural refactors (see *Re-Indexing Entire Vaults*).

### Removing & Moving Content

Keep the index in step with the filesystem **per file**, so you avoid paying for full rebuilds:
```sh
ki rm <file uri or path>           # remove one document or folder from the index
ki mv <old uri/path> <new path>    # rename / move a document or folder — updates the graph in place, links preserved
```
`ki rm` dispatches on its target: a **document** uri/path removes that one doc; a **vault** uri/dir removes the whole vault (typed confirmation — see *Other Operations*). `ki mv` updates the moved doc's path/uri without reparsing, so inbound links stay intact.

<!-- req (NEEDED — full re-index lag is too long for routine edits):
  - `ki index <file>`: accept a single file path/uri → incremental upsert of one document (today ki index is folder/vault-level only).
  - `ki rm <doc uri|file>`: document-level removal, dispatching vault-vs-doc on target type (today ki rm is vault-only and errors on sub-vault targets — revisit docs/index_rm_behavior.md).
  - `ki mv <old> <new>`: new command; move/rename a document in the graph in place (path + uri update, links preserved) without a reparse.
  All must be fast/incremental, NOT full-vault rebuilds. -->

### Re-Indexing Entire Vaults

Re-indexing entire vaults can become an expensive operations with more documents, but is sometimes necessary as updating/adding/removing individual content pieces may miss other changes made by the user (or accidentally by you).

After significant changes or refactors to knowledge base content

Run `ki status`; if it reports `STALE`, run `ki index .` — preferably in a sub-agent. 

During indexing, the entire vault is removed from Neo4j then rebuilt to reflect what's on the file system currently. The process can last a couple seconds (for dozens of documents) or a few minutes (for thousands of docs).  During re-indexing the the `ki vault` should not be used for search or answering questions.

### Other Operations

- `ki configure` / `ki profile list` — manage Neo4j connection profiles.
- `ki vault list` — inspect indexed vaults (uri, description).
- `ki use <profile>` — set / change the vault's profile binding.
- `ki init <path>` — (advanced) write `.ki/vault.yaml` without indexing.
- `ki nuke` — reset the entire graph (all vaults); typed confirmation, last resort.
- `ki skill …` — install this skill bundle into other agents.

## Anti-Patterns

1. **Raw file ops on vault content** — reading/searching a vault with `Read` / `grep` / `cat` instead of `ki`, and spawning sub-agents that default to file reads. Burns the tokens `ki` exists to save.
2. **Defaulting the profile** — auto-picking `default_profile` (or any profile) when binding/indexing a vault. Profiles are privacy boundaries; confirm the profile once, at first usage in session. 
3. **Full re-index for a small change** — running `ki index .` (full nuke + rebuild) after editing one file. Use per-file `ki index <file>` / `ki rm` / `ki mv`; reserve `ki index .` for bulk edits or refactors.
4. **Cold-starting Neo4j just to look around** — spinning up a stopped profile's instance only to enumerate vaults.
5. **Switching vault or profile mid-task without confirming** — work one vault + one profile per session; confirm any switch with the user.
6. **Querying a vault while it's re-indexing**, or **fabricating URIs** — copy URIs from `ki outline` / `ki search`; never guess them.



## Vault and File Indexing

Vaults must be indexed to....
Vaults should be Re-indexed whenever files in theior sorc]isponding directories change to staty upo-to-sync.
### Trigger When

### Trigger After
Always get a refreshed Vault outline after re-index

## When to Use Ki
`ki` is useful for doing 5 things faster & with less tokens then normal file ops ....
1. searching - ...
2. navigating ....
4. summarizing ...
3. getting: read sections or subsections of markdown file contents without needing to open and scan entire source files


### Search (...move to reference file)

### Trigger When
...?

#### Search Steps
1. ALWAYS check the vault outline first to see if there are documents or sections to focus on
   1. If  nothing looks relevant move on to ki search otherwise collect uris and dig into deeper outlines
   2. drill down on deeper outlines on any elements there with ki outline <uri> this will give you an outline starting at that element as root.  COuld be a folder, a document or section/subsection inside a document
   3. Use web search to follow any external links if relevant
2. use ki search --under vault...etc
3. 




## ALWAYS Remember
Keep these fresh; if you don't know, **rerun** to recover:

1. **Active profile & vault** → `ki status` (cwd-derived). Sub-agents: tell them to `cd` into the vault dir, then run `ki status` — nothing needs to be passed in the prompt.
2. **Vault outline** → `ki outline <vault uri>`.
3. **Indexing state** → GOOD / LIKELY_STALE / RE_INDEXING / DOWN (from `ki status` / `ki profile list`).

Active context is **per-vault** (the `profile:` bound in `.ki/vault.yaml`) and **per-shell** (cwd), so parallel sessions on different vaults never collide — there's no single global "active" to clobber.


## Recommended Usage Patterns
1. Only work with one vault in one profile at a time during a session


## How to invoke

The commands you'll actually use:

```bash
ki configure                     # one-time per machine: writes ~/.config/ki/config.yaml
ki index ./path/to/vault         # sync a folder into the graph (re-index = full nuke + re-ingest)
ki search "query" [flags]        # retrieve via fulltext
ki outline ["<uri>"]             # render the containment tree (B.12) — see "When to invoke ki outline"
                                 #   (`ki tree` is a kept alias — same command, same flags)
ki get "<uri>" [flags]           # fetch metadata + content at a Doc / Section URI — see "When to invoke ki get"
ki vault list                    # show every indexed vault with its description (routing hint)
ki rm ./path/to/vault            # remove an entire vault from the index (vault-only — see below)
ki nuke                          # reset the entire graph + schema (typed confirmation required)
```

**Sync model — vault-level only.** ki keeps the *vault* as the only unit of sync. `ki index <vault>` adds-or-fully-refreshes a vault's content (re-index = nuke the vault's contents, then re-ingest). `ki rm <vault>` removes a whole vault. There's no document-level or subtree-level rm — that granularity isn't exposed. If a user wants stale docs cleaned up after deleting files on disk, the answer is `ki index <vault>` (it'll nuke + rebuild). See `docs/index_rm_behavior.md` for the design rationale.

**Passing a file path or subdirectory to `ki rm` errors** with a message that points at `ki index` — surface that to the user verbatim, don't try a workaround.

Also available: `ki init <path>` (advanced: write the vault marker without indexing), and `ki skill {list, install, remove, print}` for installing this routing-rules file into other agents (Cursor, Windsurf, etc.). See `ki skill list` for the full agent catalog.

### Picking a search mode

`ki search` runs across **all three** node types by default (`:Document`, `:Section`, `:Vault`) and returns the top-`k` results overall, sorted by fulltext score. Narrow with `--types` when you know which granularity you want:

| User intent                                              | Command                                                | Underlying query |
|----------------------------------------------------------|--------------------------------------------------------|------------------|
| *"What did I write about X?"* (cast a wide net)          | `ki search "X"` (default — all three types)            | B.1 + B.2 + B.11 merged by score |
| *"What did I write about X?"* (finest grain only)        | `ki search "X" --types section`                        | B.2 — section content fulltext |
| *"Find the doc called Y"* / *"the note where I…"*        | `ki search "Y" --types document --k 5`                 | B.1 — document title fulltext  |
| *"Which of my vaults is about X?"* (cross-vault routing) | `ki search "X" --types vault --k 5`                    | B.11 — vault fulltext over `description` |
| *"What's related to this doc?"* / *"what links to X?"*   | (not wired — see *Capabilities not yet wired*)         | — |

Flag mechanics:
- `--types` is a comma-separated subset of `{document,section,vault}`. Omit to default to all three. Combine arbitrarily: `--types section,vault`.
- `--k N` is the **total** result cap across all selected types — not per-type. Each underlying query is run with the same `k`; results are merged, sorted by score, and capped to `N` rows total.
- `--json` emits a machine-readable list. The list is heterogeneous — each row keeps its native B.1 / B.2 / B.11 shape plus a `label` field (`"Document"` / `"Section"` / `"Vault"`). Key off `label` (or off the `document_uri` / `section_uri` / `vault_uri` field) to identify each row's type.
- `--profile <name>` overrides the default Neo4j connection profile (also via `KI_PROFILE=<name>`).

Plain-text output uses the same `T` letter convention as `ki outline`: `V`=Vault, `D`=Document, `S`=Section. The `uri` column carries the load-bearing identifier you can paste into `ki get <uri>` or `ki outline <uri>`.

**Document results now include external URLs and internal non-md files** (#37). A markdown link like `[Launch blog](https://neo4j.com/blog/...)` creates an external `:Document` keyed by the URL itself; `[Slides](./deck.pptx)` creates an internal stub `:Document` (`sourceType=LOCAL_FILE`, no content, just metadata + fileHash). `ki search --types document` and `ki outline` surface all three Document kinds (internal md, internal non-md stub, external URL). `ki get <external-url>` works and returns the external Document's metadata; the URI is the URL itself — no slug prefix.

**Cross-type score caveat.** Fulltext scores are not strictly comparable across queries (different term-frequency normalization per set size), so the merged ranking is a heuristic. If a query feels off, re-run with `--types <one>` to see the native ranking for that type alone.

The `--type neighbors` flag (1-hop `LINKS_TO` traversal via B.3) was removed in 0.4.0. To see what a specific doc/section links to, use `ki outline "<uri>" --depth 1` — outbound `LINKS_TO` edges render as horizontal branches by default. For backlinks ("what links *to* this?"), there is no CLI surface yet — see [#35](https://github.com/zach-blumenfeld/knowledge-index/issues/35).

### When to invoke `ki outline`

`ki outline` renders the containment hierarchy (Vault → Folder → Document → Section) plus outbound `LINKS_TO` edges, as a table-of-contents-style terminal output. See `docs/outline-format.md` for the exact format.

> **Naming.** `ki outline` is the canonical command name as of v0.5.0. `ki tree` is kept as a permanent alias — same flags, same behavior — so existing skill bundles, blog posts, and muscle memory keep working. Prefer `ki outline` in new code and prose.

Use `ki outline` when:
- **Search is returning weak results** and you want to see what's actually in the vault. The order to escalate: `ki search` → `ki search` with query-expansion alternates → `ki outline`. The outline shows you what docs / headings exist so you can re-search with better terms.
- **You need to understand the vault's structure** before navigating (e.g. *"summarize what's in this vault"*, *"is there a folder for X?"*).
- **You want to see what a doc/section links to.** `ki outline "<uri>" --depth 1` surfaces outbound `LINKS_TO` from that node as horizontal branches.

```bash
ki outline                             # render every indexed vault, depth 4
ki outline "my-notes"                  # render one specific vault (URI is the slug)
ki outline "Vault:my-notes"            # same, with the Label: prefix (issue convention)
ki outline "<doc-uri>" --depth 2       # render a doc and its section subtree
ki outline --full                      # also show vault description sub-lines

# Back-compat: the v0.4.x form keeps working.
ki outline --at "<uri>" --depth 2      # `--at` is now a fallback for the positional URI
ki tree "<uri>" --depth 2              # `ki tree` is a permanent alias for `ki outline`
```

**Row order is meaningful.** Folders and Documents under a parent are alphabetical by `name`. Sections under a Document or another Section are in **reading order** (NEXT_SECTION), not alphabetical — the first child section is the one that appears first in the source file. `LINKS_TO` siblings are alphabetical by target URI. See `docs/outline-format.md` *Sibling ordering*.

### When to invoke `ki get`

`ki get` is the **content fetch** step in the canonical chain `ki search` / `ki outline` → URI → `ki get`. Once you have a URI for a Document or Section, `ki get` returns its metadata + (optionally) its content.

```bash
ki get "<uri>"                          # default --type content
ki get "<uri>" --type path              # metadata only — read the file via `path`
ki get "<uri>" --type content           # node's stored content (preamble + child URI pointers per Rule 1)
ki get "<uri>" --type full              # reconstructed reading-order body (B.4 for Documents, B.14 for Sections)
ki get "<uri-a>" "<uri-b>" "<uri-c>"    # batch — multiple URIs in one invocation
ki get "<uri>" --json                   # machine-readable; always includes `path` (from #40)
```

**Pick `--type` by intent:**

| You want                                                              | Flag                 |
|-----------------------------------------------------------------------|----------------------|
| Just the metadata so you can `Read` the file yourself                 | `--type path`        |
| The node's own content + URI handles to drill into children           | `--type content` (default) |
| The reconstructed full reading-order body in one call                 | `--type full`        |

`--type content` returns the node's stored `content` field. Per Content Construction Rule 1, that's the *preamble text directly under this node's heading* followed by `uri:` references to direct children — child body text is **not** inlined. Drill into a child URI with another `ki get`, or escalate to `--type full` to get the whole subtree in one query.

`--type full` does a single bounded Cypher walk (no client-side recursion) — cheap even on long documents. Use it when you actually want the bytes back without a second round trip.

**`ki get` only accepts `:Document` and `:Section` URIs.** Passing a `:Folder` URI errors and points at `ki outline <uri>` (enumeration is what folders are for). Passing a `:Vault` URI errors and points at `ki vault list` / `ki outline <uri>`. Passing an unknown URI errors and cites the URI verbatim.

### Walking the URI schema

`ki` URIs encode the containment hierarchy as a path. `ki outline`'s URI column shows the **full URI** for every row — no shorthand — so you can copy a URI directly out of the outline and feed it straight back into `ki outline <uri>` or `ki get <uri>`.

**Vault URIs are human-readable slugs.** Derived from the vault directory's basename on first ingest (e.g. `~/my-notes` → `my-notes`). On collision with an existing vault in the same Neo4j, ki appends `-1`, `-2`, etc — max+1 over currently-present slugs. So a fresh basename-`my-notes` vault becomes `my-notes-3` if `my-notes`, `my-notes-1`, `my-notes-2` are already taken. Deleting a vault (`ki rm --vault`) frees its slug for reassignment, so a long-lived agent skill referencing `my-notes-2/foo.md` could silently re-point at a different vault if `my-notes-2` is removed and a new same-basename vault ingests. Treat the URI as opaque once assigned — read it from `ki vault list` or the row in `ki outline` rather than guessing.

You can also derive **ancestor** URIs by trimming, without any "go up" query:

| URI shape                                                                                                  | Trim to get parent                                                          |
|------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| `my-notes/projects/foo.md#h1-slug/h2-slug` (nested section)                                                | Trim `/h2-slug` → `my-notes/projects/foo.md#h1-slug` (the parent Section).  |
| `my-notes/projects/foo.md#some-heading` (top-level section)                                                | Trim `#some-heading` → `my-notes/projects/foo.md` (the owning Doc).         |
| `my-notes/projects/foo.md`                                                                                 | Trim `/foo.md` → `my-notes/projects` (the parent Folder).                   |
| `my-notes/projects`                                                                                        | Trim `/projects` → `my-notes` (the Vault root, just the slug).              |
| `my-notes`                                                                                                 | No further trim — Vault is the top. (`User` is not surfaced in URIs.)       |

Section URI fragments encode the **full heading path** (`<h1-slug>/<h2-slug>/...`), so trimming the last `/<segment>` of the fragment gives the parent section's URI — and trimming the whole `#...` gives the owning Doc.

To **expand** any inferred URI, run `ki outline "<that-uri>" --depth N`. To **search within** a subtree, [#36](https://github.com/zach-blumenfeld/knowledge-index/issues/36) tracks the `--under` scoping flag; until it ships, search cross-vault and filter results by URI prefix.

### Multi-vault routing

When the user has more than one indexed vault, start with `ki vault list` (or `ki search "<topic>" --types vault`) to pick the right one. There is no CLI flag yet to scope a follow-up `ki search` to that vault ([#36](https://github.com/zach-blumenfeld/knowledge-index/issues/36) tracks `--under`); for now, run the cross-vault search and filter results client-side by `document_uri` prefix matching the chosen vault's URI, or use `ki outline "<vault-uri>"` to navigate within the chosen vault.

If a vault has no `description:` set, `ki` emits a warning at index time and on every `ki search --types vault` / `ki vault list` result. Treat that as a prompt to *ask the user* what the vault is for, then write it in one command:

```bash
ki index <vault> --description "One or two sentences on what's in this vault and when an agent should pick it."
```

`--description` refuses to overwrite an existing one — add `--force-description` to replace. If you'd rather edit the YAML by hand, the file is `<vault>/.ki/vault.yaml`:

```yaml
uri: <existing slug — do not touch>
description: |
  ...
```

(If asking the user isn't possible, `ki outline "<vault-uri>"` lets you skim the vault's structure and propose a description for confirmation.)

### Query expansion for semantic equivalence

`ki search` is fulltext on `displayName + content + aliases`. Wikilink display texts get folded into target aliases at ingest, so vaults that link `[[Darth Vader|Anakin]]` match "Anakin" already — but cultural / world-knowledge synonyms the *vault* never spells out won't.

**When to expand.** Top-`k` results look weak: zero hits, a single hit with a low fulltext score, or no document-level match for what was clearly a document-level query.

**How to expand.** Rewrite the user's term to a small set of plausible alternates you know from world knowledge (e.g. "Anakin" → also try "Darth Vader", "Vader", "Skywalker"; "JFK" → also try "John F Kennedy", "Kennedy"). Run alternates as additional `ki search` calls, or one OR-form Lucene query: `ki search 'JFK OR "John F Kennedy" OR Kennedy'`.

**Limits.** This relies on what *you* know. Personal-vault aliases ("BB" = "Project Bluebird") won't be expanded this way unless the user has linked them in their notes — in which case the ingest-side wikilink-alias path already covers them.

Example:

```bash
ki search Anakin --json          # 0 hits
ki search 'Anakin OR "Darth Vader" OR Vader' --json   # retry expanded
```

### If `ki` isn't installed yet

```bash
curl -sSfL https://knowledge-index.ai/install.sh | bash   # installs ki + neo4j-cli + agent skills
ki --version
```

Safe to run unattended in agent auto-mode (idempotent, per-user).

## Auto-mode rules

- **Reversible, local actions: auto-fire.** Installing `ki`, `ki index`, single-doc and subtree `ki rm`, `ki skill install`, bringing up the Local Neo4j container (see below). Report what you did after the fact.
- **Irreversible / billable actions: pause for explicit consent.** Whole-vault `ki rm --vault`, `ki configure → Aura` (creates a billable cloud resource), anything that requires the user to type a confirmation.
- **Picking a Neo4j on first run.** Rule of thumb: **Local (Podman)** is right for solo / on-this-laptop work, **Aura** is for sharing an index across machines or a team, **Existing** is for "the user already has a Neo4j running, just point at it." Order to try:
  1. **An existing reachable Neo4j** (env vars, a profile already in `~/.config/ki/config.yaml`, or a container/service already on `:7687`) — use `ki configure` option `3) Existing` and report what you connected to.
  2. **Otherwise, the Local (Podman) path** — `ki configure` option `1) Local (neo4j w/ podman)`. Reversible/local; auto-fires *if* `podman` is on PATH and `:7687` is free. If `podman` isn't installed, pause and surface the install one-liner from `references/neo4j-podman.md` (Preflight). If `:7687` is occupied by something other than our container, fall back to option 3.
  3. **Aura is never silent.** Only pick `2) Aura` if the user explicitly asked for cloud / Aura, or there's already an Aura profile. *"Build me a knowledge base"* is consent for the goal, not for creating cloud resources.
- **Recovery when `ki` can't reach Neo4j (Local-Podman profiles).** Diagnose the container state and act, all idempotent:
  - `podman ps -a --filter name=neo4j-ki` → stopped → `podman start neo4j-ki`. Data intact. Auto-fire.
  - Container missing, `podman volume ls --filter name=neo4j-ki-data` shows the volume → re-run the *Bring up Neo4j* block in `references/neo4j-podman.md`. Data intact. Auto-fire.
  - Container missing **and** volume missing → re-run *Bring up Neo4j*, then re-run `ki index <path>` for every vault the user had indexed (the indexes are gone). If you don't have the vault paths in conversation history, ask the user.
  - Full recipes (including macOS `podman machine start` after reboot) live in `references/neo4j-podman.md` *Recovery* and *After a reboot*.
- **File-system side-effects on the user's vault: never.** `ki` doesn't touch source files; the agent doesn't either, except for writing converted-markdown output to a user-approved folder (see PREPARE).

## Capabilities not yet wired

The retrieval shapes reachable today are: `ki search` (B.1 + B.2 + B.11 — fulltext across all three node types by default, narrow with `--types`), `ki outline` (B.12 + B.12-links — containment + outbound `LINKS_TO`), and `ki get` (B.4 / B.13 / B.14 — node metadata + reading-order content for a Document or Section URI). If a user asks for something `ki` doesn't currently expose — **backlinks** (#35), **subtree-scoped search** (`--under`, #36), section windowing, shortest path, vector / semantic search, native non-markdown ingest, MCP-bridged chat-app access — **don't pretend you'll run it**.

Instead:

1. Tell the user the capability isn't wired today.
2. Suggest the closest wired alternative if there is one (e.g. for "what does this doc link to?" → `ki outline "<uri>" --depth 1`).
3. Point them at the open issues for the roadmap: <https://github.com/zach-blumenfeld/knowledge-index/issues>.

The full Cypher for each unwired retrieval shape exists in `docs/retrieval-queries.md`, so if the user really needs the answer once, they can run the query directly against Neo4j — but that's an explicit fallback, not something `ki` invokes for them.

**Chat-app surfaces** (claude.ai, ChatGPT, Gemini, Copilot Web/Desktop) have no shell access and can't call `ki` at all. Suggest the user run a coding agent (you, in Claude Code) on the same machine, or paste `ki search "..." --json` output into the chat manually.

## Cross-references

- Full design spec: `docs/requirements_v01_mvp.md`
- Schema (nodes / edges / properties): `docs/data-model.md`
- What gets written on `ki index`: `docs/ingest-cypher.md`
- What gets returned by `ki search`: `docs/retrieval-queries.md`
