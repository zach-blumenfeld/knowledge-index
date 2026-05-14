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
- find connections / backlinks / related notes across their writing
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

Note: `ki` is intentionally **cross-vault by default**. Multiple vaults indexed to the same Neo4j are searchable together via `ki search`; per-vault scoping is opt-in via flags. Don't skip `ki` because the request spans multiple folders / projects / vaults — that's the case it's *built for*.

## How to invoke

The commands you'll actually use:

```bash
ki configure                     # one-time per machine: writes ~/.config/ki/config.yaml
ki index ./path/to/vault         # sync a folder into the graph (idempotent; auto-creates the vault marker)
ki search "query" [flags]        # retrieve via fulltext + graph traversal
ki rm ./path/to/file.md          # remove a document from the index (source file untouched)
ki rm ./path/to/vault --vault    # remove a whole vault from the index (source files untouched)
```

Also available: `ki init <path>` (advanced: write the vault marker without indexing), and `ki skill {list, install, remove, print}` for installing this routing-rules file into other agents (Cursor, Windsurf, etc.). See `ki skill list` for the full agent catalog.

### Picking a search mode

`ki search` takes `--type` to choose the retrieval shape. Match the flag to the user's intent:

| User intent                                              | Flag                                            | Underlying query |
|----------------------------------------------------------|-------------------------------------------------|------------------|
| *"What did I write about X?"* (default; finest grain)    | `--type section` (default)                      | B.2 — section content fulltext |
| *"Find the doc called Y"* / *"the note where I…"*        | `--type document --k 5`                         | B.1 — document title fulltext  |
| *"What's related to this doc?"*                          | `--type neighbors --doc-uri <uri> --k 2`        | B.3 — 1-hop `LINKS_TO` neighbourhood |

Add `--json` for machine-readable output. `--k` is the result limit (or hop depth for `neighbors`). `--profile <name>` overrides the default Neo4j connection profile (also via `KI_PROFILE=<name>`).

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

If a user asks for one of these and `ki` is the right tool, explain that the underlying query exists but the CLI flag isn't shipped in v1 — don't pretend you'll run it. Tracked in the *Roadmap & known limitations* section of the project README.

- Backlinks ("which docs link to this one?") — query B.9 in `docs/retrieval-queries.md`, not on the CLI.
- Full document text in reading order — query B.4.
- ±N section windowing — queries B.7 / B.8.
- Shortest path between two documents — query B.10.
- Vector / semantic search — fulltext is the only retrieval substrate in v1.
- Talking to `ki` from claude.ai, ChatGPT, Gemini, or Copilot Web/Desktop — these chat surfaces have no shell access. Suggest the user run a coding agent (you, in Claude Code) on the same machine, or paste `ki search "..." --json` output into the chat manually. A native MCP server is roadmap.

## Cross-references

- Full design spec: `docs/requirements_v01_mvp.md`
- Schema (nodes / edges / properties): `docs/data-model.md`
- What gets written on `ki index`: `docs/ingest-cypher.md`
- What gets returned by `ki search`: `docs/retrieval-queries.md`
