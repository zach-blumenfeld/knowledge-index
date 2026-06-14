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
│   ▸ runs `ki configure | index | search | tree | get | drop` │
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

The schema is normative in `docs/data-model/schema.md`; this is the picture.

**Nodes** — every non-User node is MERGEd on a single `uri` property:

| Label      | `uri` shape                                                    | Holds                                                              |
|------------|----------------------------------------------------------------|--------------------------------------------------------------------|
| `User`     | (internal `id`, not in URIs)                                   | `displayName`, `email`, `*SeenAt` timestamps                       |
| `Vault`    | human-readable slug (`my-notes`, collision: `-1` / `-2`)       | `name`, `displayName`, `path`, `isObsidianVault`, `description`    |
| `Folder`   | `<vault-slug>/<folder-path>`                                   | `name`, `displayName`, `path`                                      |
| `Document` | `<vault-slug>/<file-path-within-vault>` (internal `.md`) <br> `<vault-slug>/<file-path-within-vault>` (internal non-md stub — PDF, deck, image; `sourceType=LOCAL_STUB`) <br> `https://...` or `file:///...` (external; `sourceType=URL_LINK`) | `displayName`, `path`, `aliases`, `fileHash`, frontmatter, content |
| `Section`  | `<doc-uri>#<h1-slug>/<h2-slug>/...`                            | `displayName`, `headingLevel`, `content`, `aliases`                |

URIs encode containment as paths — trimming the last segment yields the parent, so the agent can derive ancestor URIs without a "go up" query.

**Three Document kinds** (since PR #50 / #37 landed): every `[text](href)` link in a markdown file now creates a `:Document` node. Internal `.md` targets resolve to the existing in-vault Document; internal non-md files (PDFs, decks, images) become stub Documents inside the containment tree; external URLs become Documents *outside* the containment tree (the `HAS` single-parent invariant applies to vault-belonging nodes only). Same URL referenced from multiple vaults collapses to one Document via `MERGE` on `uri`. See `docs/data-model/link_capture.md` for the full matrix and edge cases.

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
5. **Vault-level sync (nuke-then-rebuild).** If `.ki/vault.yaml` already existed before this run (i.e. it's a re-index, not a fresh vault), `remove_vault` is called against the existing `Vault.uri`: every Document / Section / Folder / `LINKS_TO` / `LOADED` edge for this vault is `DETACH DELETE`d in batched chunks. Fresh vaults skip this step. This is the v0.4.0 sync model — there's no per-doc diff; re-index is always a full rebuild. See `docs/data-model/index_rm_behavior.md` for the rationale.
6. **Materialize the folder layer.** Walk the discovered doc paths, MERGE `Folder` nodes + `HAS` edges so the containment tree exists before docs land.
7. **Parse + write each doc.** `markdown-it-py` produces tokens with source-line spans, which the section-tree builder uses to slice body text per heading cleanly. Per-doc writes batch via `UNWIND $rows AS row` — Document, Section, `HAS`, `HAS_SECTION`, `NEXT_SECTION`, `LOADED`.
8. **Link-capture post-pass.** All `[[wikilinks]]` and `[md](./link.md)`-style markdown links are resolved against the now-materialized vault and written as `LINKS_TO` edges. Two extra batches MERGE the new Document kinds: internal non-md stubs (`./deck.pptx` → stub `:Document` with `sourceType=LOCAL_STUB`, attached to its containing folder via `HAS`) and external Documents (`https://...` or vault-escaping `file://...` → Document outside the containment tree, `sourceType=URL_LINK`). Display text from piped wikilinks (`[[Doc|alias]]`) and external links (`[Launch blog](https://...)`) is folded into the target's `aliases` so fulltext queries hit alternates the vault never explicitly spells out.

The `Content Construction Rules` in `docs/data-model/schema.md` are load-bearing here: Section `content` is **only** the preamble under that heading plus `uri:` pointers to child Sections — child body text is *not* inlined. The agent reconstructs full body text on demand via `ki get --type full` (which walks `NEXT_SECTION` server-side in one Cypher call).

### Batching + OOM resilience

`src/ki/ingest/batcher.py` is a thin loop over `UNWIND $rows AS row` writes. Default batch is 1,000 rows. On a Neo4j `TransientError` containing "out of memory", the batcher halves the batch size (floor 16), retries the failed slice once, and continues at the smaller size. The batcher emits one warning per ingest on first shrink so the user knows their Neo4j heap is the bottleneck.

### `ki drop <vault-path>` and `ki nuke`

Two removal verbs, no per-doc / subtree granularity (v0.4.0 vault-level sync model — see `docs/data-model/index_rm_behavior.md`):

- **`ki drop <vault-path>`** — remove a whole vault from the index. Typed confirmation of the vault display name required. Source files are never touched. Passing a file path or a subdirectory under a vault errors with a message pointing at `ki index` — re-indexing nukes-and-rebuilds, which is how stale docs get cleaned up after the user deletes files on disk.
- **`ki nuke`** — reset the entire graph: every Vault, every Document, every Section, every edge, plus the schema constraints + fulltext index, get torn down and recreated. Typed confirmation required. Use when something has gone wrong at the schema level or when starting over.

## Two read paths

`src/ki/search/queries.py` holds the wired retrieval queries (full Cypher in `docs/data-model/retrieval-queries.md`; the user-facing search model in `docs/commands/search.md`).

### `ki search "query" [--types]`

**One** ranked fulltext sweep over Documents **and** Sections at once (`SEARCH_DOC_SECTION` over the `content_search` index) — not a per-type merge. A Document's `content` is just its header/intro; the body lives in its Sections, so sweeping both is the default.

`--types <subset>` narrows to one or both of `{document, section}` — there is no `vault` type (vaults are matched only via their `description` for routing, never returned as results). `--k N` caps the result set; `--json` emits rows with a `label` field. Document hits span all three Document kinds — internal markdown, internal non-md stubs, and external URLs — so *"launch blog"* can surface an externally-linked URL by the link text the user wrote (stub nodes themselves are post-filtered; the citing doc surfaces — see `docs/commands/search.md` §6).

Scope follows the local/remote model (`docs/scoping.md`, `docs/commands/search.md`): the vault you're in by default, `--under` to narrow locally, `--profile` / `--vault` to reach a profile remotely. (`--under` and the multi-uri scope predicate are the next build step.)

### `ki outline ["<uri>"]`

Walks the containment tree via **B.12** (`HAS` traversal, `NEXT_SECTION` sort-position for sections) plus **B.12-links** (outbound `LINKS_TO` as horizontal branches). Default renders every vault, depth 4; the positional URI argument (or back-compat `--at`) roots the walk at a specific vault/folder/doc/section URI. Sibling ordering is alphabetical for Folders/Documents, **reading order** for Sections (because that's what `NEXT_SECTION` encodes), alphabetical-by-target for `LINKS_TO`. `ki tree` is a permanent alias.

### `ki get "<uri>"`

Content fetch by URI. Three `--type` modes:

- `--type path` — metadata only (so the agent can `Read` the file via the `path` property).
- `--type content` (default) — the node's stored `content` field per Rule 1 (preamble + child URI pointers, *not* inlined child body).
- `--type full` — reconstructed reading-order body via **B.4** (Documents) or **B.14** (Sections). Server-side walk — one round trip even for long documents.

Only accepts `:Document` and `:Section` URIs. `:Folder` and `:Vault` URIs error with a hint pointing at `ki outline` or `ki vault list`.

## Configuration & connection

`~/.config/ki/config.yaml` holds named profiles. Each profile is a `(name, uri, user, password, source)` tuple, file mode `0600`. The `source` field is a label (`local-podman | aura | existing`) — nothing dispatches on it; it's there so the user (or a future feature) knows where the connection came from.

Vaults reference profiles by **name**. Credentials never live inside a vault, so syncing a vault folder via Dropbox / iCloud / git doesn't leak them.

`ki configure` is an interactive wizard with three paths:

- **`1) Local (neo4j w/ podman)`** — shells out to `podman` to run `neo4j:latest` in the canonical container (`neo4j-ki`, named volume `neo4j-ki-data`, `--restart unless-stopped`, plugins `apoc` + `genai`). The full runbook + recovery procedures live in `skills/knowledge-index/references/neo4j-podman.md`. `src/ki/neo4j_podman.py` mirrors the canonical values — both must agree.
- **`2) Aura`** — shells out to `neo4j-cli aura create` (billable cloud resource).
- **`3) Existing`** — prompts for URI + credentials.

`ki configure --yes` picks option 1 (Local) non-interactively. Aura is *never* picked silently — even on auto-mode it requires explicit consent.

## Agent integration

`ki` is designed for two modes of use: a human typing commands, and a coding agent invoking `ki` via shell. The agent-as-user contract lives in `skills/knowledge-index/SKILL.md` (which ships bundled with the package and gets dropped into each agent's well-known config path by `ki skill install`).

`SKILL.md` defines:

- **TRIGGER when** — user prompts that should route to `ki` ("track our conversations in memory", "what did I write about X?", "build a knowledge base for me").
- **PREPARE when** — agent-side conversion of non-markdown sources (PDF / docx / HTML) into markdown the user owns, *then* `ki index`.
- **SKIP when** — ephemeral session memory, source-file mutation, or non-markdown content the user doesn't want converted.
- **Auto-mode rules** — what the agent may do unattended vs. what requires explicit consent. Reversible/local actions (`ki index`, `ki skill install`, bringing up the Local Podman container) auto-fire; billable or destructive ones (`ki configure → Aura`, whole-vault `ki drop`) pause.
- **Recovery** — what the agent does when `ki` fails to connect on a Local-Podman profile: `podman start` → re-`podman run` with same volume → re-index if the volume is gone.

**Chat-app surfaces** (claude.ai, ChatGPT, Gemini, Copilot Web/Desktop) can't shell out to `ki` and aren't supported in v1. The path forward is an MCP server bridging the chat surface to a local `ki`.

## Read order for new contributors

A reading order if you want to get up to speed:

1. **This doc** — the connect-the-dots overview you're reading.
2. **`AGENTS.md`** — design principles, project map, the *Don't* list.
3. **`docs/scoping.md`** — profiles, vaults, config, the command surface, and the local/remote scoping model. **`docs/general-philosophy.md`** — the design principles. (The original `docs/archive/requirements_v01_mvp.md` is historical — read these instead.)
4. **`docs/data-model/schema.md`** — the schema. Normative on node properties, edge directions, content-construction rules.
5. **`docs/commands/search.md`** + **`docs/commands/get.md`** + **`docs/commands/outline.md`** — the read-surface depth docs (search, get, outline). **`docs/data-model/ingest-cypher.md`** + **`docs/data-model/retrieval-queries.md`** — the working Cypher that `src/ki/ingest/queries.py` and `src/ki/search/queries.py` lift from.
6. **`docs/data-model/index_rm_behavior.md`** + **`docs/data-model/link_capture.md`** — the v0.4.0 sync model (`ki drop` vault-only, `ki index` re-index = nuke + rebuild) and the link-capture matrix (three Document kinds). Newest specs; not in the original requirements doc.
7. **`skills/knowledge-index/SKILL.md`** — the agent-as-user contract. When changing CLI shape, update this in the same PR.
8. **`skills/knowledge-index/references/neo4j-podman.md`** — the Local Neo4j runbook. Source of truth for the Podman container/volume/image/plugin choices that `src/ki/neo4j_podman.py` mirrors.

## Deferred — what's not wired and the closest workaround

- **Vector search / embeddings** — v2. Fulltext (`content_search`) is the v1 substrate. The `genai` plugin is enabled in the Podman setup so existing vaults won't need re-ingest when this lands.
- **Backlinks** ("what links *to* this?") — #35. No wired alternative; Cypher exists in `retrieval-queries.md` for a one-shot.
- **Subtree-scoped search** (`--under`) and remote `--vault` scoping — #36. Designed in `docs/scoping.md` / `docs/commands/search.md`; not yet wired. Workaround: search the vault you're in, then filter by uri prefix.
- **MCP server** for chat-app integration — roadmap. Use a coding agent on the same machine in the meantime.
- **Native non-markdown ingest** (PDF, docx, HTML) — roadmap. Agent-side `PREPARE` handles this today: convert to `.md`, save to a user-approved folder, `ki index` it.
- **OS keyring credential storage** — v2. Plaintext + mode `0600` in `~/.config/ki/config.yaml` for v1.

## Scalability envelopes

Scalability envelopes (also enforced by `scripts/gen_test_vault.py`):

| Size     | Files  | Bytes   | Initial index target               |
|----------|--------|---------|------------------------------------|
| `tiny`   | ~20    | ~100 KB | sub-second                         |
| `small`  | ~200   | ~10 MB  | seconds                            |
| `medium` | ~2,000 | ~200 MB | minutes                            |
| `large`  | 10,000 | ~1 GB   | < 5 min on a developer laptop      |

Single-machine ingest, single Neo4j write session. The throughput at v1 scales doesn't justify the complexity of concurrent writers (which would deadlock on shared `MERGE` targets anyway).

Test fixtures are deterministic — `scripts/gen_test_vault.py --size <size> --seed 42` produces byte-identical output, so tests can depend on specific filenames and link targets.
