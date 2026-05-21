# Architecture

A connect-the-dots overview of how `ki` works end-to-end. If you're new to the project, read this before the more granular specs in `docs/`. If you've been here a while, this is the "where does X live" map.

`ki` (CLI for `knowledge-index`) is a personal knowledge index over folders of markdown files. The model: point `ki` at a vault, it parses every `.md` into a graph (documents, sections, links), and gives you fulltext-backed retrieval verbs to query it. Source files are never mutated — `ki` is an index, not a document store.

## Non-negotiable design principles

These shape every decision below. From `AGENTS.md`:

1. **`ki` is an index, not a document store.** All output lives in `~/.config/ki/`, Neo4j, or `.ki/vault.yaml`. Never edits the user's `.md` files (with one narrow exception — `--description` writes the `description:` field in `.ki/vault.yaml` on explicit request).
2. **The backend is opaque to the user.** Cypher, node labels, traversal — none of it surfaces in default output. From the user's perspective `ki` is a search tool.
3. **One source of truth per concern.** Config in `~/.config/ki/config.yaml`, vault identity in `.ki/vault.yaml`, graph in Neo4j. No parallel state.
4. **Safe by default, dangerous by flag.** Destructive ops require explicit flags + typed confirmation. Billable cloud ops require explicit consent even on agent auto-mode.

## Layers at a glance

```
┌──────────────────────────────────────────────────────────────┐
│  User / Agent                                                │
│   ▸ runs `ki configure | index | search | tree | get | rm`   │
└────────────────────────────┬─────────────────────────────────┘
                             │
┌────────────────────────────┴─────────────────────────────────┐
│  ki (Python, Click-driven)                                   │
│                                                              │
│   src/ki/cli.py        ── command wiring                     │
│   src/ki/commands/     ── one module per command             │
│   src/ki/config.py     ── ~/.config/ki/config.yaml + Profile │
│   src/ki/vault.py      ── .ki/vault.yaml + URI scheme        │
│   src/ki/parser/       ── markdown-it-py + frontmatter       │
│   src/ki/ingest/       ── pipeline, batcher, queries, prov.  │
│   src/ki/search/       ── retrieval queries (B.1–B.14)       │
│   src/ki/neo4j_client  ── driver lifecycle + ensure_schema   │
│   src/ki/neo4j_podman  ── Local-path container wrapper       │
└────────────────────────────┬─────────────────────────────────┘
                             │  Bolt
┌────────────────────────────┴─────────────────────────────────┐
│  Neo4j (Community)                                           │
│   nodes:  User · Vault · Folder · Document · Section         │
│   edges:  USES_VAULT · LOADED · HAS · NEXT_SECTION · LINKS_TO│
│   index:  content_search (fulltext over D|S|V)               │
└──────────────────────────────────────────────────────────────┘
```

Three places state lives, and only three: the user's `.md` files on disk (read-only), `.ki/vault.yaml` markers in each vault (vault identity + optional description), and Neo4j (everything else). `ki` itself is stateless between invocations.

## The graph

The schema is normative in `docs/data-model.md`; this is the picture.

**Nodes** — every non-User node is MERGEd on a single `uri` property:

| Label      | `uri` shape                                                    | Holds                                                              |
|------------|----------------------------------------------------------------|--------------------------------------------------------------------|
| `User`     | (internal `id`, not in URIs)                                   | `displayName`, `email`, `*SeenAt` timestamps                       |
| `Vault`    | human-readable slug (`my-notes`, collision: `-1` / `-2`)       | `name`, `displayName`, `path`, `isObsidianVault`, `description`    |
| `Folder`   | `<vault-slug>/<folder-path>`                                   | `name`, `displayName`, `path`                                      |
| `Document` | `<vault-slug>/<file-path-within-vault>`                        | `displayName`, `path`, `aliases`, `fileHash`, frontmatter, content |
| `Section`  | `<doc-uri>#<h1-slug>/<h2-slug>/...`                            | `displayName`, `headingLevel`, `content`, `aliases`                |

URIs encode containment as paths — trimming the last segment yields the parent, so the agent can derive ancestor URIs without a "go up" query.

**Edges:**

- `USES_VAULT` — `User → Vault`. Who has this vault registered.
- `LOADED` — `User → Vault | Document`. Per-ingest provenance bag (agentName, agentVersion, os, hostname, modelId, ...). Parallel edges allowed — keyed by `loadId`.
- `HAS` — `Vault | Folder | Document | Section → Folder | Document | Section`. Single-parent containment tree.
- `NEXT_SECTION` — `Section → Section`. DFS reading order so the agent can reconstruct full body text.
- `LINKS_TO` — `Document | Section → Document | Section`. Outbound link references (`[[wikilink]]`, `[md](./link.md)`, embeds).

**Constraints + index** (created idempotently by `ensure_schema` in `neo4j_client.py`):

- Unique constraints on `User.id`, `Vault.uri`, `Folder.uri`, `Document.uri`, `Section.uri`.
- One fulltext index, `content_search`, over `Document | Section | Vault` on `[displayName, content, aliases, description]`. **This is the v1 retrieval substrate.** No vector indexes yet (see *Deferred* below).

## Two write paths

### `ki index <path>` — the ingest pipeline

`src/ki/ingest/pipeline.py` is the per-vault orchestrator. Order matters:

1. **Discover.** Walk the folder for `.md` files, skipping `.git`, `.obsidian`, `.ki`, `node_modules`, etc. Size-guard: refuse files past the configured cap.
2. **Read concurrently.** `aiofiles`-backed, bounded by a concurrency knob. Read-side is the only place parallelism happens — writes are serialized.
3. **Open one write session.** Single Neo4j session for the whole vault ingest. Concurrent writers would deadlock on shared `MERGE` targets at v1 scales.
4. **Per-vault upsert.** `ensure_schema`, then MERGE the `User`, `Vault`, `USES_VAULT`, `LOADED` triple.
5. **Materialize the folder layer.** Walk the discovered doc paths, MERGE `Folder` nodes + `HAS` edges so the containment tree exists before docs land.
6. **Fetch existing `fileHash`es** for this vault. Documents whose SHA-256 matches what's already in the graph are skipped (path refresh only). This is the idempotency lever — re-indexing a vault is cheap.
7. **Parse + write each changed doc.** `markdown-it-py` produces tokens with source-line spans, which the section-tree builder uses to slice body text per heading cleanly. Per-doc writes batch via `UNWIND $rows AS row` — Document, Section, `HAS`, `HAS_SECTION`, `NEXT_SECTION`, `LOADED`.
8. **LINKS_TO post-pass.** Wikilinks and markdown links are resolved against the (now fully-materialized) vault, then written as `LINKS_TO` edges in one batch. Display text from piped wikilinks (`[[Doc|alias]]`) is folded into the target's `aliases` so fulltext queries hit alternates the vault never explicitly spells out.

The `Content Construction Rules` in `docs/data-model.md` are load-bearing here: Section `content` is **only** the preamble under that heading plus `uri:` pointers to child Sections — child body text is *not* inlined. The agent reconstructs full body text on demand via `ki get --type full` (which walks `NEXT_SECTION` server-side in one Cypher call).

### Batching + OOM resilience

`src/ki/ingest/batcher.py` is a thin loop over `UNWIND $rows AS row` writes. Default batch is 1,000 rows. On a Neo4j `TransientError` containing "out of memory", the batcher halves the batch size (floor 16), retries the failed slice once, and continues at the smaller size. The batcher emits one warning per ingest on first shrink so the user knows their Neo4j heap is the bottleneck.

### `ki rm <path> [--vault] [--keep-marker]`

Removes nodes from the index. Source files are never touched. Three blast radii:

- **Single doc** — no prompt.
- **Subtree** — prompts with a count.
- **Whole vault** — requires `--vault` *and* typed confirmation of the vault display name.

`--keep-marker` preserves `.ki/vault.yaml` so the next `ki index` rebuilds onto the same `Vault.uri` — the natural "reset this vault" idiom.

## Two read paths

`src/ki/search/queries.py` holds five wired retrieval queries. The full Cypher for each is in `docs/retrieval-queries.md`.

### `ki search "query" [--types]`

The default merges three fulltext queries by score:

- **B.1** — Document title fulltext.
- **B.2** — Section content fulltext (joins back to the owning Document for ancestry).
- **B.11** — Vault fulltext (over `name + displayName + description`, the cross-vault routing query).

`--types <subset>` narrows to one or two of `{document, section, vault}`. `--k N` is a *total* cap across the merged result set (not per-type). `--json` emits a heterogeneous list with a `label` field per row.

**Cross-type score caveat:** fulltext scores aren't strictly comparable across queries because of term-frequency normalization. Merge is a heuristic — re-run with `--types <one>` if the ranking looks off.

### `ki tree [--at "<uri>"]`

Walks the containment tree via **B.12** (`HAS` traversal, `NEXT_SECTION` sort-position for sections) plus **B.12-links** (outbound `LINKS_TO` as horizontal branches). Default renders every vault, depth 4; `--at` roots the walk at a specific vault/folder/doc/section URI. Sibling ordering is alphabetical for Folders/Documents, **reading order** for Sections (because that's what `NEXT_SECTION` encodes), alphabetical-by-target for `LINKS_TO`.

### `ki get "<uri>"`

Content fetch by URI. Three `--type` modes:

- `--type path` — metadata only (so the agent can `Read` the file via the `path` property).
- `--type content` (default) — the node's stored `content` field per Rule 1 (preamble + child URI pointers, *not* inlined child body).
- `--type full` — reconstructed reading-order body via **B.4** (Documents) or **B.14** (Sections). Server-side walk — one round trip even for long documents.

Only accepts `:Document` and `:Section` URIs. `:Folder` and `:Vault` URIs error with a hint pointing at `ki tree` or `ki vault list`.

## Configuration & connection

`~/.config/ki/config.yaml` holds named profiles. Each profile is a `(name, uri, user, password, source)` tuple, file mode `0600`. The `source` field is a label (`local-podman | aura | existing`) — nothing dispatches on it; it's there so the user (or a future feature) knows where the connection came from.

Vaults reference profiles by **name**. Credentials never live inside a vault, so syncing a vault folder via Dropbox / iCloud / git doesn't leak them.

`ki configure` is an interactive wizard with three paths:

- **`1) Local (neo4j w/ podman)`** — shells out to `podman` to run `neo4j:latest` in the canonical container (`neo4j-ki`, named volume `neo4j-ki-data`, `--restart unless-stopped`, plugins `apoc` + `genai`). The full runbook + recovery procedures live in `references/neo4j-podman.md`. `src/ki/neo4j_podman.py` mirrors the canonical values — both must agree.
- **`2) Aura`** — shells out to `neo4j-cli aura create` (billable cloud resource).
- **`3) Existing`** — prompts for URI + credentials.

`ki configure --yes` picks option 1 (Local) non-interactively. Aura is *never* picked silently — even on auto-mode it requires explicit consent.

## Agent integration

`ki` is designed for two modes of use: a human typing commands, and a coding agent invoking `ki` via shell. The agent-as-user contract lives in `skills/ki/SKILL.md` (which ships bundled with the package and gets dropped into each agent's well-known config path by `ki skill install`).

`SKILL.md` defines:

- **TRIGGER when** — user prompts that should route to `ki` ("track our conversations in memory", "what did I write about X?", "build a knowledge base for me").
- **PREPARE when** — agent-side conversion of non-markdown sources (PDF / docx / HTML) into markdown the user owns, *then* `ki index`.
- **SKIP when** — ephemeral session memory, source-file mutation, or non-markdown content the user doesn't want converted.
- **Auto-mode rules** — what the agent may do unattended vs. what requires explicit consent. Reversible/local actions (`ki index`, `ki skill install`, bringing up the Local Podman container) auto-fire; billable or destructive ones (`ki configure → Aura`, whole-vault `ki rm --vault`) pause.
- **Recovery** — what the agent does when `ki` fails to connect on a Local-Podman profile: `podman start` → re-`podman run` with same volume → re-index if the volume is gone.

**Chat-app surfaces** (claude.ai, ChatGPT, Gemini, Copilot Web/Desktop) can't shell out to `ki` and aren't supported in v1. The path forward is an MCP server bridging the chat surface to a local `ki`.

## Read order for new contributors

A reading order if you want to get up to speed:

1. **This doc** — the connect-the-dots overview you're reading.
2. **`AGENTS.md`** — design principles, project map, the *Don't* list.
3. **`docs/requirements_v01_mvp.md`** — the full design spec. Normative on CLI shape, scalability envelopes, auto-mode rules.
4. **`docs/data-model.md`** — the schema. Normative on node properties, edge directions, content-construction rules.
5. **`docs/ingest-cypher.md`** + **`docs/retrieval-queries.md`** — the working Cypher. `src/ki/ingest/queries.py` and `src/ki/search/queries.py` lift from these.
6. **`skills/ki/SKILL.md`** — the agent-as-user contract. When changing CLI shape, update this in the same PR.
7. **`references/neo4j-podman.md`** — the Local Neo4j runbook. Source of truth for the Podman container/volume/image/plugin choices that `src/ki/neo4j_podman.py` mirrors.

## What's in flight, what's deferred

**In flight** (open PRs at time of writing):

- *Capture all markdown links as `:Document` nodes* (#37 / PR #50). Broadens the link parser from `.md`-target-only to every `[text](href)`. External URLs become `:Document` nodes outside the containment tree (`sourceType=URL_LINK`); internal non-md files (PDFs, decks) become stub `:Document` nodes inside the tree. The `HAS` single-parent invariant gets an amendment: it applies to vault-belonging nodes only. Expect the data-model and parser sections of this doc to evolve when that lands.

**Deferred, with the closest wired alternative noted:**

- **Vector search / embeddings** — v2. Fulltext (`content_search`) is the v1 substrate. The `genai` plugin is enabled in the Podman setup so existing vaults won't need re-ingest when this lands.
- **Backlinks** ("what links *to* this?") — #35. No wired alternative; Cypher exists in `retrieval-queries.md` for a one-shot.
- **Subtree-scoped search** (`--under <vault|folder|doc>`) — #36. Workaround: cross-vault search, filter results client-side by `document_uri` prefix.
- **MCP server** for chat-app integration — roadmap. Use a coding agent on the same machine in the meantime.
- **Native non-markdown ingest** (PDF, docx, HTML) — roadmap. Agent-side `PREPARE` handles this today: convert to `.md`, save to a user-approved folder, `ki index` it.
- **OS keyring credential storage** — v2. Plaintext + mode `0600` in `~/.config/ki/config.yaml` for v1.

## Scalability envelopes

From `docs/requirements_v01_mvp.md` *Scalability*:

| Size     | Files  | Bytes   | Initial index target               |
|----------|--------|---------|------------------------------------|
| `tiny`   | ~20    | ~100 KB | sub-second                         |
| `small`  | ~200   | ~10 MB  | seconds                            |
| `medium` | ~2,000 | ~200 MB | minutes                            |
| `large`  | 10,000 | ~1 GB   | < 5 min on a developer laptop      |

Single-machine ingest, single Neo4j write session. The throughput at v1 scales doesn't justify the complexity of concurrent writers (which would deadlock on shared `MERGE` targets anyway).

Test fixtures are deterministic — `scripts/gen_test_vault.py --size <size> --seed 42` produces byte-identical output, so tests can depend on specific filenames and link targets.
