# Knowledge Index (`ki`)
> Search index for agent memory — knowledge graph index for your documents

## Human Description
Index markdown files in a knowledge graph, then search from agent or CLI. 

Scale across multiple folders, vaults, and users.

CLI for instant setup integration and one line `index` and `search` commands. 

Backed by Neo4j for performance. 

## Agent Description
Point `ki` at a folder of markdown files; it builds a searchable knowledge graph in Neo4j you can query from the CLI or any agent.

**Agent: invoke `ki` when a user asks to:**
- track conversations or notes in memory across sessions
- remember / document something for use later
- build a knowledge base around a topic
- incorporate a folder of notes / vault / documents into durable memory
- search or recall information they've saved

**Prepare when:** source content isn't markdown. v1 indexes `.md` files only — first convert non-markdown sources (PDF / docx / HTML / plaintext) to markdown using `pandoc`, `markitdown`, or by reading + transcribing them, save the output to a folder the user picks, then run `ki index` on it.

Two working commands: `ki index` (sync a folder into the graph) and `ki search` (retrieve with full-text + graph traversal).


## Core design principle: ki is an index, not a document store

`ki` **never mutates source documents.** The user's markdown files are owned by the user (and by their editor, their git, their Dropbox sync, their Obsidian vault) — `ki` only reads them and maintains a *derived index* in Neo4j. Anything `ki` writes lives in `~/.config/ki/` (config) or in Neo4j (the index), or — in the single exception — in `.ki/vault.yaml`, a small file carrying vault identity (`uri:`, written once on first ingest) plus optional user-authored vault-level metadata such as `description:`. `ki` writes `uri:` on first creation, and writes other user-authored fields **only when the user explicitly asks** via a flag (e.g. `ki index --description "..."`). Without such a flag, every key besides `uri:` is read-only.

Practical consequences:
- No `ki drop --purge`, no `ki rewrite`, no "fix this frontmatter," no "auto-organize my notes." If a feature requires writing into a `.md` file, it's out of scope.
- A vault can be deleted entirely from the index (`ki drop`) and the source files are untouched.
- Conversely, `rm -rf my-vault/` leaves the graph in Neo4j stale until the next `ki index` — that's fine, the user is in charge of their files.
- This is what makes the tool safe to point at an Obsidian vault, a git repo, or a research folder without thinking twice.

## Name & framing

**`knowledge-index`** — picked over `graph-vault` / `vtaper` because the agent should classify it as *"the place I store/retrieve durable knowledge,"* not as *"a graph tool."* The backend (Neo4j, fulltext, graph traversal) is opaque to the agent.

The four canonical prompts this is meant to catch:
- "Can you track conversations in memory?"
- "Can you document this process for use later?"
- "Can you start building a knowledge base for me around [topic X]?"
- "Can you incorporate these notes/vault/etc. into your memory/process?"

Name alone isn't the load-bearing signal for tool selection — the skill description, `TRIGGER when:` / `SKIP when:` clauses, and example prompts do most of the work. Name primarily affects user-facing trust ("I'll use the knowledge-index" reads obviously) and cross-session recall.

## Install

```bash
# Recommended — install ki globally so it's on PATH from anywhere
uv tool install knowledge-index

# Or run one-off without installing (uvx caches the package)
uvx knowledge-index --help
```

If `uv` isn't installed yet:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Agent install (auto-mode)

If a user asks an agent to install `ki`, the safe, reversible sequence is:

```bash
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install knowledge-index
ki --version
```

Three commands, idempotent, ~10 seconds end-to-end. Reversible via `uv tool uninstall knowledge-index`. Per-user (no `sudo`), no system-state mutation — safe for agent auto-mode without confirmation.

### Packaging

Primary: **PyPI** (Python, built with `hatchling`). The Node ecosystem gets an optional `npx @zach-blumenfeld/knowledge-index` wrapper that shells out to the Python CLI — same dual-publish pattern as `create-context-graph`. Standalone binaries / Homebrew / `curl | sh` installers are deferred to v2 if real demand appears; for a Python CLI, `uvx` is already the install script.

## CLI shape

Short alias: **`ki`** (knowledge-index). Full name `knowledge-index` is the package name on PyPI/npm; `ki` is the binary users actually type.

```bash
ki configure                # one-time: writes ~/.config/ki/config.yaml with Neo4j connection
ki configure --profile work # add/edit a named profile

mkdir my-vault              # plain folder, no special tooling
echo "some ideas" >> my-vault/ideas.md
ki index ./my-vault         # syncs to Neo4j (idempotent; auto-creates .ki/vault.yaml on first run)
ki search "..." [flags]     # fulltext across {Document,Section,Vault} (B.1+B.2+B.11); --types narrows
ki outline ["<uri>"]        # render the containment hierarchy (Vault → Folder → Doc → Section) — see docs/outline-format.md
                            #   `ki tree` is a permanent alias; `--at <uri>` still works as a back-compat flag.
ki get "<uri>" [flags]      # fetch metadata + content at a Doc / Section URI; --type {path,content,full}
ki drop ./my-vault                    # remove an entire vault from the index (vault-only; source files untouched)
ki nuke                             # reset the entire graph + schema (typed confirmation required)

ki index ./my-vault --profile work    # explicit profile override
KI_PROFILE=work ki index ./my-vault   # env-var override (for scripts / agents / cron)
```

**Navigation + fetch loop.** `ki search` and `ki outline` return URIs; `ki get <uri>` fetches what those URIs point to. `--type content` (default) returns the node's stored content + `uri:` references to direct children (per Rule 1 in `docs/data-model.md`); `--type full` returns the full reading-order body via B.4 / B.14; `--type path` returns the metadata shell only, so the agent can `Read` the file via the `path` property. `ki get` only accepts `:Document` and `:Section` URIs — `:Folder` and `:Vault` URIs error with a hint pointing at `ki outline` / `ki vault list`.

**Three commands users actually need: `ki configure`, `ki index`, `ki drop`.** `ki configure` is run once per machine (or per new Neo4j connection); `ki index` and `ki drop` are the working verbs.

### Auto-sense on `ki index`

`ki index` is intentionally do-the-right-thing on first run rather than gated behind a separate init step. Specifically:

- **Missing `.ki/vault.yaml`** → auto-create the marker (a tiny YAML file containing the assigned `uri:` slug; reversible with `rm -rf .ki/`). Prints a one-line notice: *"Initialized vault at ./my-vault (uri: my-vault)."* The file is also where the user can add an optional `description:` to give agents a routing hint about what this vault is for; `ki` is read-only w.r.t. that field except when `--description` is passed.
- **Missing `~/.config/ki/config.yaml`** → drop into the `ki configure` flow interactively. On agent auto-mode, default to the Local (Podman) path and tell the user (see *Agent auto-mode behavior* below).
- **Re-index of unchanged files** → skip via `Document.fileHash` (SHA-256 stored per document; that's literally what `fileHash` is for in the schema). Only changed / new files hit Neo4j.

### `ki init` (optional, advanced)

A thin alias that writes `.ki/vault.yaml` *without* indexing. Useful only in narrow cases — e.g., pre-creating the marker so it's committed to git before any content exists. Not part of the quick-start; most users never run it.

### Removal (`ki drop`, `ki nuke`)

ki keeps the **vault** as the only unit of sync. `ki drop` operates only on vault-level targets; sub-vault granularity (doc, subtree) is not exposed. The motivation and the full removal-routine spec live in **`docs/index_rm_behavior.md`** — read that for the design.

```bash
ki drop ./my-vault                       # remove a whole vault by path (typed confirmation required)
ki drop my-vault-slug                    # same, by Vault.uri slug
ki drop <target> --dry-run               # show what would be removed; touch nothing
ki drop <target> --yes                   # skip the typed-display-name confirm
ki drop <target> --keep-marker           # remove vault data but keep .ki/vault.yaml
                                       #   (next `ki index` rebuilds onto the same Vault.uri)
ki drop <target> --chunk-size N          # rows per batched-remove transaction (default 1000)

ki nuke                                # reset the entire graph + drop ki-owned schema
ki nuke --keep-marker                  # same, but keep every .ki/vault.yaml on disk
ki nuke --dry-run / --yes / --chunk-size N
```

**Sub-vault `ki drop` is rejected with a clear error** pointing the user at `ki index <vault>` — that's the only way to sync content at file granularity (a full re-index nukes + re-ingests the vault).

**Defaults driven by safety and reversibility:**

- **Source files are never touched.** `ki drop` / `ki nuke` remove nodes from Neo4j; that's all. If the user wants files gone, they use `rm`. (See *Core design principle*.)
- **Vault removal requires typed display-name confirmation**; `ki nuke` requires typed `"nuke"` confirmation. Both bypass with `--yes`.
- **Marker stays unless `--keep-marker=False`.** `ki drop` removes `.ki/vault.yaml` by default; `ki nuke` removes every known marker by default. Pass `--keep-marker` to preserve so the next `ki index` rebuilds onto the same `Vault.uri` — the "reset this vault" idiom.
- **`LOADED` provenance edges are removed with their endpoints** via `DETACH DELETE`. Provenance is moot once the entity is gone; if anyone needs ingest history, it's reconstructable from logs.

**Agent auto-mode handling:** `ki drop` (whole vault) and `ki nuke` require explicit user consent every time, regardless of harness permission, because they destroy graph state. See *Agent auto-mode behavior* for the full partition.

## Configuration & Neo4j setup

**Config lives at the user level, never in the vault.**
```
~/.config/ki/config.yaml    # primary location (XDG)
~/.ki/config.yaml           # fallback for non-XDG systems
```
- Single source of truth per machine — no walk-up discovery, no shell sourcing.
- Holds **named profiles**, one per Neo4j connection (uri, user, password, vector-index settings later). A `default` profile if you only have one.
- File mode `0600` (same as `~/.aws/credentials`); password in plaintext for v1. Upgrade path is OS keyring.
- Vaults reference profiles by **name** (a string), not by file path — so any number of vaults scattered anywhere can point at the same profile, with zero ambiguity. Credentials never live inside a vault, so syncing a vault via Dropbox/iCloud/git doesn't leak them.

**`ki configure` flow** — interactive prompts that wrap underlying CLIs rather than reimplementing them:
```
$ ki configure
No Neo4j connection found. Set one up?

  1) Local (neo4j w/ podman) → runs `neo4j:latest` in a local Podman container (APOC + GenAI plugins); full runbook at `skills/knowledge-index/references/neo4j-podman.md`
     Best for: solo work on this laptop
  2) Aura                    → wraps `neo4j-cli aura create` (cloud — billable; creates a real instance) see https://github.com/neo4j-labs/neo4j-cli
     Best for: sharing an index across machines or a team
  3) Existing                → prompts for URI + credentials
     Best for: pointing at a Neo4j you already run

Choice [1]:
```

Option 1 shells out to `podman` (canonical container `neo4j-ki`, named volume `neo4j-ki-data`, `--restart unless-stopped`); option 2 shells out to `neo4j-cli`. Neither reimplements lifecycle / version pinning / health checks. The Podman path is preferred over a raw `docker run` because (a) Podman is rootless by default, (b) the named volume + `--restart` policy give data persistence and reboot-survival without systemd plumbing, and (c) the agent runbook in `skills/knowledge-index/references/neo4j-podman.md` covers recovery for the three failure modes (container stopped / removed / volume wiped) the agent has to handle on auto-mode.

## Agent auto-mode behavior

**Principle: autonomy ≠ permission to do irreversible things on someone's behalf.** Auto-mode lifts UX friction; it doesn't lift agent judgment about real-world side effects.

**Auto without asking** (reversible, local, no cost):
- Bring up the Local Neo4j container via `podman` (the `ki configure → Local` path; reversible via `podman stop neo4j-ki && podman rm neo4j-ki && podman volume rm neo4j-ki-data`). See `skills/knowledge-index/references/neo4j-podman.md`.
- Write `~/.config/ki/config.yaml` with the resulting credentials.
- Write `.ki/vault.yaml` markers (auto-create with `uri:` only — leave any user-authored fields alone).
- Index the vault.
- Re-run idempotent operations.

**Pause even on auto-mode** (irreversible / billable / account-touching):
- **Provisioning an Aura tenant.** Even if the user said "build me a knowledge base," that's consent for the *goal*, not for *creating cloud resources*. Default to local; surface an explicit offer to switch to Aura.
- Anything creating a third-party account.
- Operations the user can't reverse with `rm`.

**Disambiguator**: if the user says "build me a knowledge base **on Aura**" or there's already an Aura profile in the config, that's explicit consent — proceed. Without that, local is the auto-mode default.

**Surface even on auto-mode**:
- One-line after-the-fact notice: *"Started Neo4j locally via podman; credentials in `~/.config/ki/config.yaml`."* Transparency, not approval-gating.
- Errors (port `:7687` in use, `podman` not installed). Auto-mode should not mean silent failure — pause and surface the install one-liner / port-collision diagnosis from `skills/knowledge-index/references/neo4j-podman.md`.
- The fact that I made a local-vs-cloud decision, so the user can override.

**Preference learning**: if the user says once "always default to Aura for ki" or "never use cloud Neo4j," save a feedback memory and honor it across sessions without re-asking.

**In-band CLI escape hatch**: `ki configure --yes` skips prompts and picks the default. On agent auto-mode, the agent passes `--yes` and the prompts never surface; off auto-mode, prompts render and the agent answers them with the user.

## Key design decisions

### Vault identity via marker file
Vault `uri` is a **human-readable slug** derived from the vault directory's basename, written to `.ki/vault.yaml` on first ingest (mirrors `.git/`, `.obsidian/`, JetBrains `.idea/`). The marker travels with the folder, so a vault synced across machines via Dropbox / iCloud / git resolves to the **same** `:Vault` node — independent of user and machine. This makes `USES_VAULT` load-bearing: multiple users can use the same vault. On collision (another vault on the same Neo4j has already claimed the base slug), a `-N` suffix is appended where N is one more than the highest existing suffix in the family. The algorithm operates on currently-present slugs, so deleting a vault frees its slug for later reassignment (permanent never-reuse would need a tombstone scheme; not in 0.4.0). Basenames that don't contain any alphanumeric content (e.g. `~/___`) are refused with a clear error asking the user to rename the folder. The same file optionally carries a user-authored `description:` that flows into `Vault.description` on each ingest (latest write wins) and powers `ki search --types vault`.

### Node schema: User / Vault / Document / Section
All non-User nodes identified by `uri` (single-property MERGE key):
- `Vault.uri` = human-readable slug from marker file (slugified basename, `-N` suffix on collision)
- `Document.uri` = `<vaultId>/<file path within vault>`
- `Section.uri` = `<vaultId>/<file path within vault>#<slugified heading path>`

User is *not* in the URI — load provenance lives on the `LOADED` edge.

### `NEXT_SECTION` for linear reading order
`HAS_SECTION` gives the tree; `NEXT_SECTION` gives reading order. The chain threads **all** sections of a document in DFS order, crossing heading levels (last descendant of an `H1` → next `H1`, not a sibling at the same level). Rebuilt per ingest (delete-then-recreate). Makes "give me the whole doc" and `±N` windowing trivial linear walks.

### Batched ingest via `UNWIND $rows AS row`
Documents, sections, and edges all use the standard `UNWIND` pattern — driver-side batches of 1–5k rows per transaction. `LOADED` provenance props (`agentName`, `agentVersion`, `os`, `hostname`, …) are lifted out of `UNWIND` into a single `$loadProps` map so they aren't duplicated `N`× per row. One `$loadId` UUID is shared between the User→Vault and User→Document `LOADED` edges produced by a single ingest, so a single `loadId` retrieves the full ingest event.

### Fulltext as v1 retrieval substrate (no embeddings)
`content_search` fulltext index over `Document|Section|Vault` on `displayName + content + aliases + description`. Vector indexes deferred. Indexing `aliases` lets wikilink alternates ("JFK", "John F Kennedy") hit the same doc; indexing `description` on `:Vault` enables `ki search --types vault` for agent routing across vaults. Neo4j silently skips missing properties per label, so the same index serves all three node types.

## Scalability

`ki` should handle a realistic personal vault without surprises and without exotic infrastructure. The numbers below set the v1 envelope; the levers below describe how the implementation gets there.

### Target envelopes for v1

- Single vault: up to **10,000 markdown files** / **1 GB of content**.
- Single document: up to **1 MB** / **~10,000 sections**. Files above the threshold are skipped with a warning, not silently truncated (see lever 6).
- Re-index of an **unchanged** vault: **< 5 seconds** (fileHash skip makes this near-instant).
- Initial index of a **10k-document vault**: **< 5 minutes** on a developer laptop against local Neo4j (Podman container per `skills/knowledge-index/references/neo4j-podman.md`).

### Neo4j heap sizing

The canonical Local-Podman container ships with **`heap_max__size=1G` + `pagecache_size=512M`** — total Neo4j footprint ~2 GB, sized so `ki` is a good citizen alongside the user's other apps on a personal laptop. The batcher's OOM auto-recovery (halve-and-retry at floor 16) absorbs the occasional fat transaction, so 1 GB heap covers most of the v1 envelope without tuning. If you're running Neo4j elsewhere (Aura, an existing Docker container, a remote instance) or hitting "batch size shrunk to N" warnings frequently, use this as the rough sizing guide:

| Vault size                    | Recommended JVM heap (max)              | Recommended page cache |
|-------------------------------|------------------------------------------|------------------------|
| up to ~5k docs / ~500 MB      | 1 GB *(canonical default)*               | 512 MB                 |
| ~5k–10k docs / ~500 MB–1 GB   | 2 GB *(bump if shrink warnings appear)*  | 512 MB – 1 GB          |

Always set heap and page cache **together** — Neo4j's pre-flight refuses to start if `heap + pagecache + native overhead > container memory`. Setting only heap leaves the page cache to auto-tune, which can blow the budget on a constrained Podman VM and crash-loop the container.

On macOS, the Podman machine's RAM is the outer constraint: `podman machine init --memory 4096` (4 GB) leaves enough headroom for the canonical ~2 GB Neo4j commit plus container overhead. See `skills/knowledge-index/references/neo4j-podman.md` *Preflight* and *Resizing the Podman machine*.

### Levers (in order of impact)

1. **`Document.fileHash` (SHA-256) skip on unchanged files.** Most re-indexes touch <1% of files; everything else short-circuits before any Cypher runs. This is the biggest single win and is already in the schema.
2. **Configurable batched `UNWIND $rows AS row` ingest, default 1,000 rows / transaction.** Expose `--batch-size N`. Optimal batch size is **bounded by Neo4j's configured heap**, not by Python memory, and depends on per-row payload (a vault of small notes can batch 5–10×bigger than one of long-form documents). Heuristic: ~1,000 is safe on a small local instance (Neo4j Community defaults); on Aura the right number scales with RAM. YOu should be able to see via neo4j-cli, but if not, as a general heuristic, assume Aura PRO and above tiers can comfortably handle 5,000+ rows / batch and ingest. Tune empirically; there is no general "right" answer.
3. **Concurrent file reading via `asyncio` + `aiofiles` (or `concurrent.futures.ThreadPoolExecutor`) with bounded parallelism.** Reading is I/O-bound and benefits from concurrency. Default ~16 workers; configurable. This is the only place concurrency lives.
4. **Process one document at a time end-to-end** (parse → batch → write → release). Peak parse-side memory is bounded by the largest single file's section tree, not by the whole vault. Critical for predictability — vault size grows linearly in time, memory stays flat.
5. **Single Neo4j write session — no concurrent writes.** Even two concurrent writers can deadlock on shared `MERGE` targets (`Vault`, `User`, parent `Section` for `HAS_SECTION`). Batching does the heavy lifting; concurrency on writes is a foot-gun with no real throughput payoff at v1 scales.
6. **Per-file size guard (Python-side OOM defense).** Pre-check file size *before* parsing. Default skip threshold: **10 MB per file**, configurable via `--max-file-size`. Files exceeding the threshold are listed in the run summary and excluded — never silently truncated, never partially indexed. Cheap defense against pathological inputs that would otherwise OOM the Python parser process (which the OS would kill before any code could recover).

### Two kinds of OOM — different defenses

- **Neo4j-side OOM** (transaction exceeds the database's configured heap): driver returns `TransientError: Out of memory`. **Recoverable** — Python catches it cleanly. v1 behavior should be: on this error, halve the batch size, retry the failed batch, and continue with the smaller size for subsequent batches (with a one-line warning suggesting the user pass a lower `--batch-size` next run). Levers 2 and 5 prevent it; this is the recovery path when prevention misses.
- **Python-side OOM** (parser holding a single giant file's section tree): OS kills the process; no recovery possible. **Prevented** by lever 6 (file-size guard) + lever 4 (one document at a time, parse tree released before the next).

### Explicitly NOT in v1

- **pyarrow / arrow-based pipelines.** The data is string-heavy, not columnar-numeric. The framework overhead doesn't earn its keep on markdown ingest at v1 scales.
- **Concurrent Neo4j write sessions.** Deadlock risk > throughput gain. See lever 5.
- **Distributed ingest (Ray / Dask / Spark).** Workload doesn't justify it.
- **Streaming markdown parser for huge files.** Deferred until measurements show the file-size guard isn't enough.
- **Programmatic recovery from Python-side OOM.** The OS kills the process; no `try / except` can catch it. Prevention via the file-size guard + processing one document at a time is the real defense. (Neo4j-side OOM is different — that one **is** recoverable; see *Two kinds of OOM*.)
- **Handing oversized files back to the agent for re-formatting.** Lossy (the agent has to guess split points), wasteful (re-parse on re-run), and unnecessary — the file-size guard catches the problem up-front and the agent can decide what to do without `ki` having parsed anything.

## Files in this directory

| File                          | Contents                                                                                                                                                                    |
| ----------------------------- |-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `target-data-model.md`        | Property tables (User, Vault, Document, Section) + §4.2 relationships table (`USES_VAULT`, `LOADED`, `HAS_DOCUMENT`, `HAS_SECTION`, `NEXT_SECTION`, `LINKS_TO`)             |
| `target-data-model-cypher.md` | §4.3 batched ingest queries (steps 1–6: Documents, Sections, HAS_SECTION, NEXT_SECTION clear+rebuild, User-LOADED-Document, LINKS_TO) + §4.4 constraints and fulltext index |
| `retrieval-queries.md`        | 10 retrieval queries `B.1`–`B.10` + per-query design notes; ported from the Wikipedia-graph queries in [research](research-data-model/research-retrieval-queries.md)        |
| `REQUIREMENTS.md`             | (pre-existing)                                                                                                                                                              |
| `SKILL.md`                    | (pre-existing — agent skill spec)                                                                                                                                           |
| `ingest.py`, `search.py`      | (pre-existing implementation stubs)                                                                                                                                         |

## Open questions / next steps

- `Vault.path` is currently a node property — should move to the `USES_VAULT` edge when multi-user / multi-machine ingest becomes real (each user has their own local path for a shared vault).
- Skill description text: explicit `TRIGGER when:` and `SKIP when:` clauses with 4–6 example prompts. This (not the name) is what determines whether an agent invokes the skill correctly.
- Wire `ki configure` / `ki init` / `ki index` / `ki search` CLI commands against the queries in `target-data-model-cypher.md` (§4.3 batched writes) and `retrieval-queries.md` (`B.1`–`B.10`).
- Re-ingest correctness: `NEXT_SECTION` clear-and-rebuild is correct but blunt; if section counts get large, switch to a diff-based update.
- Credential storage upgrade path: plaintext-in-`~/.config/ki/config.yaml` for v1 → OS keyring (Python `keyring` lib, or 1Password CLI integration) for v2.
- Ignore-patterns for `ki index`: hidden directories (`.git/`, `.obsidian/`, `.ki/`, anything starting with `.`) excluded by default. Open question: introduce a `.kiignore` file, reuse `.gitignore` if present, or both? See `target-data-model.md` *Path conventions* for how nested directories are encoded.
- `:Folder` nodes are intentionally absent in v1 — hierarchy lives in `Document.uri` and prefix-matches handle subtree queries. Revisit if a use case appears for folder-level metadata that isn't captured by a folder-note Document (Obsidian folder notes, Hugo `_index.md`).
- **Large test vault asset.** A deterministic 10k-file / ~1 GB Obsidian-style vault is produced by `scripts/gen_test_vault.py --size large --seed 42` and uploaded via `scripts/upload_test_vault.sh` to GitHub releases. Once the release exists, the asset URL is `https://github.com/zach-blumenfeld/knowledge-index/releases/download/v0.1.0-fixtures/vault-large.zip`. Update the tag in this line when a new release is cut.
