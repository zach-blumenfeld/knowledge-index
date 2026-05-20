# `ki` — Skill spec

Agent-facing routing rules for the `knowledge-index` (`ki`) CLI. If you are an agent reading this in response to a user's request, this file tells you when to invoke `ki` and when to take a different action.

> **Description:** Search index for agent memory — knowledge graph index for your documents. Point `ki` at a folder of markdown files; it builds a searchable knowledge graph in Neo4j you can query from the CLI or any agent.

## TRIGGER when

Invoke `ki` when a user asks to:

- track conversations or notes in memory across sessions
- remember / document something for use later
- build a knowledge base around a topic
- incorporate a folder of notes / vault / documents into durable memory
- search or recall information they've saved
- find connections / related notes across their writing (outbound `LINKS_TO` is wired; backlinks are not — see #35)
- get full text or sibling context for a section in their notes

Example user prompts that should route here:
- *"Can you track our conversations in memory?"*
- *"Can you document this process for use later?"*
- *"Can you start building a knowledge base for me around [topic X]?"*
- *"Can you incorporate these notes / this vault into your memory?"*
- *"What do my notes say about X?"*
- *"Find the document where I wrote about Y."*

## PREPARE when

Source content isn't markdown. `ki` v1 indexes `.md` files only. To handle non-markdown sources:

1. Convert non-markdown sources (PDF / docx / HTML / plaintext) to markdown first, using `pandoc`, `markitdown`, or by reading + transcribing.
2. Save the output to a folder the user picks (ask them where the first time; remember their answer for future runs).
3. Then run `ki index` on it.

The agent does the conversion; `ki` does not. This is by design — `ki` is an index, not a document store.

## SKIP when

- The user wants **ephemeral, single-session memory** (transient conversation state). Use the assistant's built-in conversation context instead.
- The user wants **Claude's own built-in agent-memory system** (`~/.claude/projects/.../memory/`, where preferences / user-feedback / project state live). That's a layer for agent-internal state; `ki` is for the user's content. They coexist — don't conflate them.
- The user wants to **modify their source files** (rename, rewrite, reorganize). `ki` never mutates sources. Use a separate file-editing flow.
- The content is not markdown **and** the user doesn't want it converted (see *PREPARE when* for the conversion path).

Note: `ki search` is **cross-vault by default**, and today that's the *only* mode — it runs across every vault indexed to the same Neo4j with no CLI-side scoping flag (a `--under <vault|folder|doc|section>` flag is tracked in [#36](https://github.com/zach-blumenfeld/knowledge-index/issues/36)). Don't skip `ki` because the request spans multiple folders / projects / vaults — that's the case it's *built for*. To narrow to a specific vault today, search cross-vault and filter the results client-side by `document_uri` prefix, or use `ki tree --at "<vault-uri>"` to navigate within a single vault's structure.

## How to invoke

The commands you'll actually use:

```bash
ki configure                     # one-time per machine: writes ~/.config/ki/config.yaml
ki index ./path/to/vault         # sync a folder into the graph (idempotent; auto-creates the vault marker)
ki search "query" [flags]        # retrieve via fulltext
ki tree [--at "<Label>:<uri>"]   # render the containment tree (B.12) — see "When to invoke ki tree"
ki get "<uri>" [flags]           # fetch metadata + content at a Doc / Section URI — see "When to invoke ki get"
ki vault list                    # show every indexed vault with its description (routing hint)
ki rm ./path/to/file.md          # remove a document from the index (source file untouched)
ki rm ./path/to/vault --vault    # remove a whole vault from the index (source files untouched)
```

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

Plain-text output uses the same `T` letter convention as `ki tree`: `V`=Vault, `D`=Document, `S`=Section. The `uri` column carries the load-bearing identifier you can paste into `ki get <uri>` or `ki tree --at <uri>`.

**Cross-type score caveat.** Fulltext scores are not strictly comparable across queries (different term-frequency normalization per set size), so the merged ranking is a heuristic. If a query feels off, re-run with `--types <one>` to see the native ranking for that type alone.

The `--type neighbors` flag (1-hop `LINKS_TO` traversal via B.3) was removed in 0.4.0. To see what a specific doc/section links to, use `ki tree --at "<uri>" --depth 1` — outbound `LINKS_TO` edges render as horizontal branches by default. For backlinks ("what links *to* this?"), there is no CLI surface yet — see [#35](https://github.com/zach-blumenfeld/knowledge-index/issues/35).

### When to invoke `ki tree`

`ki tree` renders the containment hierarchy (Vault → Folder → Document → Section) plus outbound `LINKS_TO` edges, as a table-of-contents-style terminal output. See `docs/tree-format.md` for the exact format.

Use `ki tree` when:
- **Search is returning weak results** and you want to see what's actually in the vault. The order to escalate: `ki search` → `ki search` with query-expansion alternates → `ki tree`. The tree shows you what docs / headings exist so you can re-search with better terms.
- **You need to understand the vault's structure** before navigating (e.g. *"summarize what's in this vault"*, *"is there a folder for X?"*).
- **You want to see what a doc/section links to.** `ki tree --at "<uri>" --depth 1` surfaces outbound `LINKS_TO` from that node as horizontal branches.

```bash
ki tree                                # render every indexed vault, depth 4
ki tree --at "my-notes"                # render one specific vault (URI is the slug)
ki tree --at "Vault:my-notes"          # same, with the Label: prefix (issue convention)
ki tree --at "<doc-uri>" --depth 2     # render a doc and its section subtree
ki tree --full                         # also show vault description sub-lines
```

**Row order is meaningful.** Folders and Documents under a parent are alphabetical by `name`. Sections under a Document or another Section are in **reading order** (NEXT_SECTION), not alphabetical — the first child section is the one that appears first in the source file. `LINKS_TO` siblings are alphabetical by target URI. See `docs/tree-format.md` *Sibling ordering*.

### When to invoke `ki get`

`ki get` is the **content fetch** step in the canonical chain `ki search` / `ki tree` → URI → `ki get`. Once you have a URI for a Document or Section, `ki get` returns its metadata + (optionally) its content.

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

**`ki get` only accepts `:Document` and `:Section` URIs.** Passing a `:Folder` URI errors and points at `ki tree --at <uri>` (enumeration is what folders are for). Passing a `:Vault` URI errors and points at `ki vault list` / `ki tree --at <uri>`. Passing an unknown URI errors and cites the URI verbatim.

### Walking the URI schema

`ki` URIs encode the containment hierarchy as a path. `ki tree`'s URI column shows the **full URI** for every row — no shorthand — so you can copy a URI directly out of the tree and feed it straight back into `ki tree --at <uri>` or `ki get <uri>`.

**Vault URIs are human-readable slugs.** Derived from the vault directory's basename on first ingest (e.g. `~/my-notes` → `my-notes`). On collision with an existing vault in the same Neo4j, ki appends `-1`, `-2`, etc — max+1 over currently-present slugs. So a fresh basename-`my-notes` vault becomes `my-notes-3` if `my-notes`, `my-notes-1`, `my-notes-2` are already taken. Deleting a vault (`ki rm --vault`) frees its slug for reassignment, so a long-lived agent skill referencing `my-notes-2/foo.md` could silently re-point at a different vault if `my-notes-2` is removed and a new same-basename vault ingests. Treat the URI as opaque once assigned — read it from `ki vault list` or the row in `ki tree` rather than guessing.

You can also derive **ancestor** URIs by trimming, without any "go up" query:

| URI shape                                                                                                  | Trim to get parent                                                          |
|------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| `my-notes/projects/foo.md#h1-slug/h2-slug` (nested section)                                                | Trim `/h2-slug` → `my-notes/projects/foo.md#h1-slug` (the parent Section).  |
| `my-notes/projects/foo.md#some-heading` (top-level section)                                                | Trim `#some-heading` → `my-notes/projects/foo.md` (the owning Doc).         |
| `my-notes/projects/foo.md`                                                                                 | Trim `/foo.md` → `my-notes/projects` (the parent Folder).                   |
| `my-notes/projects`                                                                                        | Trim `/projects` → `my-notes` (the Vault root, just the slug).              |
| `my-notes`                                                                                                 | No further trim — Vault is the top. (`User` is not surfaced in URIs.)       |

Section URI fragments encode the **full heading path** (`<h1-slug>/<h2-slug>/...`), so trimming the last `/<segment>` of the fragment gives the parent section's URI — and trimming the whole `#...` gives the owning Doc.

To **expand** any inferred URI, run `ki tree --at "<that-uri>" --depth N`. To **search within** a subtree, [#36](https://github.com/zach-blumenfeld/knowledge-index/issues/36) tracks the `--under` scoping flag; until it ships, search cross-vault and filter results by URI prefix.

### Multi-vault routing

When the user has more than one indexed vault, start with `ki vault list` (or `ki search "<topic>" --types vault`) to pick the right one. There is no CLI flag yet to scope a follow-up `ki search` to that vault ([#36](https://github.com/zach-blumenfeld/knowledge-index/issues/36) tracks `--under`); for now, run the cross-vault search and filter results client-side by `document_uri` prefix matching the chosen vault's URI, or use `ki tree --at "<vault-uri>"` to navigate within the chosen vault.

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

(If asking the user isn't possible, `ki tree --at "<vault-uri>"` lets you skim the vault's structure and propose a description for confirmation.)

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
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install knowledge-index
ki --version
```

Safe to run unattended in agent auto-mode (idempotent, per-user, reversible via `uv tool uninstall knowledge-index`).

## Auto-mode rules

- **Reversible, local actions: auto-fire.** Installing `ki`, `ki index`, single-doc and subtree `ki rm`, `ki skill install`. Report what you did after the fact.
- **Irreversible / billable actions: pause for explicit consent.** Whole-vault `ki rm --vault`, `ki configure → Aura` (creates a billable cloud resource), anything that requires the user to type a confirmation.
- **Picking a Neo4j on first run.** The `Local` option in `ki configure` depends on the `neo4j-local` binary, which isn't published yet — don't pick it on auto-mode. Order to try:
  1. **An existing reachable Neo4j** (env vars, a `docker ps` showing `neo4j` on `:7687`, or a profile already in `~/.config/ki/config.yaml`) — use `ki configure` option `3) Existing` and report what you connected to.
  2. **Otherwise, ask the user.** "I need a Neo4j to point ki at. Should I (a) walk you through Aura — billable cloud, or (b) wait for you to bring up a local one?" Don't pick Aura silently — *"Build me a knowledge base"* is consent for the goal, not for creating cloud resources.
- **File-system side-effects on the user's vault: never.** `ki` doesn't touch source files; the agent doesn't either, except for writing converted-markdown output to a user-approved folder (see PREPARE).

## Capabilities not yet wired

The retrieval shapes reachable today are: `ki search` (B.1 + B.2 + B.11 — fulltext across all three node types by default, narrow with `--types`), `ki tree` (B.12 + B.12-links — containment + outbound `LINKS_TO`), and `ki get` (B.4 / B.13 / B.14 — node metadata + reading-order content for a Document or Section URI). If a user asks for something `ki` doesn't currently expose — **backlinks** (#35), **subtree-scoped search** (`--under`, #36), section windowing, shortest path, vector / semantic search, native non-markdown ingest, MCP-bridged chat-app access — **don't pretend you'll run it**.

Instead:

1. Tell the user the capability isn't wired today.
2. Suggest the closest wired alternative if there is one (e.g. for "what does this doc link to?" → `ki tree --at "<uri>" --depth 1`).
3. Point them at the open issues for the roadmap: <https://github.com/zach-blumenfeld/knowledge-index/issues>.

The full Cypher for each unwired retrieval shape exists in `docs/retrieval-queries.md`, so if the user really needs the answer once, they can run the query directly against Neo4j — but that's an explicit fallback, not something `ki` invokes for them.

**Chat-app surfaces** (claude.ai, ChatGPT, Gemini, Copilot Web/Desktop) have no shell access and can't call `ki` at all. Suggest the user run a coding agent (you, in Claude Code) on the same machine, or paste `ki search "..." --json` output into the chat manually.

## Cross-references

- Full design spec: `docs/requirements_v01_mvp.md`
- Schema (nodes / edges / properties): `docs/data-model.md`
- What gets written on `ki index`: `docs/ingest-cypher.md`
- What gets returned by `ki search`: `docs/retrieval-queries.md`
