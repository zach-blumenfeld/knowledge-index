# AGENTS.md

Instructions for AI agents (Claude, Codex, Cursor, etc.) working **on** the `knowledge-index` codebase. If you are an agent being asked by a user to *use* `ki` (index their notes, search their knowledge base, etc.), read `skills/knowledge-base/SKILL.md` instead.

## What this repo is

`knowledge-index` (CLI: `ki`) is a **search index** over a folder of markdown, backed by Neo4j — it syncs the filesystem into a knowledge graph and serves fast search / navigation / retrieval. The primitive verbs are `ki index` (sync) and `ki search` (query); read/navigation (`ki outline` [alias `ki tree`], `ki get`, `ki status`, `ki vault list`), index removal (`ki drop`, `ki nuke`), and setup (`ki configure`, `ki profile list`, `ki init`, `ki skill`) sit on top.

Canonical design lives in `docs/scoping.md` (profiles, vaults, config, the command surface, and the local/remote scoping model) and `docs/general-philosophy.md` (the tenets); the schema is `docs/data-model/schema.md`. `docs/README.md` indexes everything.

## Non-negotiable design principles

These constrain every change you make. If a proposed feature violates one of these, reject the proposal rather than working around it.

1. **`ki` is an index, not a document store.** Never mutate user-owned source files (`.md`). All `ki` output lives in `~/.config/ki/` (config), Neo4j (the index), or `.ki/vault.yaml` (vault identity + optional user-authored description per vault). `ki` writes the `uri:` field on first creation and otherwise touches user-authored fields **only when the user explicitly asks** via a flag (`ki index --description "..."` writes `description:`; without the flag, `ki` is read-only). No `--purge` flag, no "auto-organize my notes," no rewriting frontmatter. See `docs/general-philosophy.md`.
2. **The backend is opaque to the user.** From the user's and the agent-as-user's perspective, `ki` is a search tool. They don't need to know about Cypher, Neo4j, or graph traversal. Don't surface backend concepts (Cypher errors, node labels, etc.) in default output.
3. **One source of truth per concern.** Config lives at `~/.config/ki/config.yaml`; vault identity + per-vault user metadata live in `.ki/vault.yaml`; graph data lives in Neo4j. Don't introduce parallel state.
4. **Safe by default, dangerous by flag.** Destructive operations (whole-vault removal) require explicit flags AND typed confirmation. Cloud-resource creation (Aura) requires explicit consent even on agent auto-mode. See `docs/general-philosophy.md` (safe by default) and `skills/knowledge-base/SKILL.md` (agent auto-mode rules).

## Project map

| Path                              | What's there                                                                                                                                                            |
|-----------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `src/ki/cli.py`                   | Click entry point. Top-level commands (`configure`, `index`, `search`, `get`, `outline` [alias `tree`], `status`, `drop`, `nuke`, `init`) + subcommand groups (`profile list`, `vault list`, `skill {list,install,remove,print}`). Each command lives in its own module under `src/ki/commands/`. |
| `src/ki/config.py`                | XDG-aware config loader; named profiles; 0600 mode on write; `KI_PROFILE` env-var override.                                                                             |
| `src/ki/profile_resolve.py`       | Resolve which profile a command uses: `--profile` → the vault's `.ki` binding → config default. `ki search` resolves stricter (see `docs/scoping.md`).                  |
| `src/ki/vault.py`                 | `.ki/vault.yaml` marker IO (slug `uri`, bound profile name, optional user-authored description); slug rules + `-N` collision; Folder/Document/Section URI construction.  |
| `src/ki/parser/markdown.py`       | markdown-it-py-based parser. Builds section tree per *Content Construction Rules* (Rule 1–3) and exposes a DFS-ordered flat list for `NEXT_SECTION`.                    |
| `src/ki/parser/frontmatter.py`    | python-frontmatter wrapper. Splits YAML frontmatter into `aliases`, `frontmatterCreatedAt`, and a JSON blob of unknown keys.                                            |
| `src/ki/ingest/pipeline.py`       | Per-vault orchestrator: schema, per-vault upsert, fileHash skip, concurrent reads (aiofiles), single Neo4j write session, one doc at a time, LINKS_TO post-pass.        |
| `src/ki/ingest/batcher.py`        | UNWIND batching + Neo4j-OOM auto-recovery (halve and retry once, continue smaller).                                                                                     |
| `src/ki/ingest/queries.py`        | Batched `UNWIND` ingest Cypher — matches `docs/data-model/ingest-cypher.md`.                                                                                            |
| `src/ki/ingest/provenance.py`     | Builds the `User` mutable bag and `LOADED` provenance bag (best-effort detection per `docs/data-model/schema.md`).                                                       |
| `src/ki/search/queries.py`        | Retrieval Cypher: `SEARCH_DOC_SECTION` (the `ki search` document+section sweep), B.12 containment tree (`ki outline`), B.4 / B.13 / B.14 (`ki get`) — see `docs/data-model/retrieval-queries.md`. |
| `src/ki/neo4j_client.py`          | Driver lifecycle, `ensure_schema`, connectivity classification.                                                                                                         |
| `src/ki/neo4j_podman.py`          | Thin wrapper around `podman` for the `ki configure → Local` path. Mirrors the canonical values (container `neo4j-ki`, volume `neo4j-ki-data`, image `neo4j:latest`, plugins APOC + GenAI) in `skills/knowledge-base/references/neo4j-podman.md`. |
| `tests/unit/`                     | Pure-Python unit tests — parser, slug, vault marker, config, batcher (mocked driver), CLI parsing, scope resolution.                                                    |
| `tests/integration/`              | End-to-end tests against a real Neo4j. Auto-skip unless `KI_TEST_NEO4J_URI` / `KI_TEST_NEO4J_USER` / `KI_TEST_NEO4J_PASSWORD` are set — to bring one up, follow `skills/knowledge-base/references/neo4j-podman.md`. |
| `tests/fixtures/sample_vault/`    | ~20-doc deterministic tiny vault covering every node property + edge type. Generated by `scripts/gen_test_vault.py --size tiny --seed 42`. Do not hand-edit — regenerate. |
| `scripts/gen_test_vault.py`       | Deterministic Obsidian-style markdown vault generator. Four sizes (`tiny` / `small` / `medium` / `large`). See *Test fixtures* below.                                   |
| `docs/README.md`                  | Index of all docs.                                                                                                                                                      |
| `docs/scoping.md`                 | Profiles, vaults, config, the command surface, and the local/remote scoping model. The CLI/scoping design spec — read before changing command behavior.                |
| `docs/general-philosophy.md`      | The non-negotiable tenets (search not storage; reconstructable cache; never mutate source; no LLM/embeddings at write time).                                             |
| `docs/data-model/schema.md`       | Neo4j schema: `User`, `Vault`, `Folder`, `Document`, `Section` nodes; `USES_VAULT`, `LOADED`, `HAS`, `NEXT_SECTION`, `LINKS_TO` edges; uri conventions + content rules. |
| `docs/data-model/ingest-cypher.md`| Batched `UNWIND` ingest queries and constraints / fulltext index. Modify here when changing what `ki index` writes to Neo4j.                                            |
| `docs/data-model/retrieval-queries.md` | Retrieval Cypher (the `ki search` doc+section sweep, containment-tree walk, get-by-uri, windowing). Modify here when changing what `ki search` / `ki outline` / `ki get` expose. |
| `docs/commands/`                  | Per-command depth docs — `search.md`, `get.md`, `outline.md` (+ `theme-format.md`, draft).                                                                              |
| `skills/knowledge-base/SKILL.md`  | Agent-as-user routing rules (Trigger / Do-Not-Use / On-First-Use). Ships with the published tool. Covers the local single-vault workflow (see `docs/skills.md`).        |
| `skills/knowledge-base/references/neo4j-podman.md` | Agent-followable runbook for the `ki configure → Local` path (Podman). Canonical values (container, volume, image, plugins, auth) here are the source of truth — `src/ki/neo4j_podman.py` must match. |
| `CLAUDE.md`                       | Claude-Code-specific notes; defers to this file.                                                                                                                        |

### Markdown parser choice — `markdown-it-py`

`markdown-it-py` ships AST-shaped tokens with `map` (source-line spans) on
each token, which is what the section-tree builder uses to slice body text
between headings cleanly. `mistune` is faster but its tokens don't expose
source ranges, which would force us back to regex/line scanning for body
extraction. `marko` is the other contender but is slower than both. Sticking
with `markdown-it-py` unless a measurement-driven reason to swap appears.

## Conventions

- **Python, built with `uv` and `hatchling`.** `uv venv && uv sync` to set up; `uv run pytest tests/` for tests; `uv run ruff check src/ tests/` for linting.
- **No `pip install`, no `python -m venv`** — use `uv` exclusively to keep environments reproducible.
- **Cypher: spec doc + implementation, kept in lockstep.** The canonical query shapes live in `docs/data-model/ingest-cypher.md` and `docs/data-model/retrieval-queries.md`; the executable copies live in `src/ki/ingest/queries.py` and `src/ki/search/queries.py`. Change the doc and the code together — they must not drift.
- **One `uri` MERGE key per node label.** Composite keys (the old `{userId, vaultId, name}` pattern) are explicitly rejected. See `docs/data-model/schema.md`.
- **Batched ingest via `UNWIND $rows AS row`.** Single-row MERGE is ~10–100× slower against Neo4j; always batch.
- **`LOADED` provenance props (`agentName`, `agentVersion`, `os`, `hostname`, ...) are lifted out of `UNWIND`** into a top-level `$loadProps` map. Don't duplicate them per row.

## Don't

- Don't add vector indexes or embeddings. Fulltext is the retrieval substrate — a deliberate tenet (`docs/general-philosophy.md`: no embeddings → vendor-neutral, instant setup), not a gap. The `genai` plugin in the Podman setup is loaded for a future upgrade path but unused.
- Don't bake conversion logic (PDF→markdown, docx→markdown) into `ki`. If the agent-as-user needs that, *the agent* does the conversion; `ki` only indexes whatever `.md` files exist. See the *On First Use* / convert-first note in `skills/knowledge-base/SKILL.md`.
- Don't add install mechanisms beyond the one-command installer (`curl -sSfL https://knowledge-index.ai/install.sh | bash`, which runs `uv tool install`) and `uvx knowledge-index`. No Homebrew formula or standalone binaries unless real demand appears.
- Don't reimplement the Neo4j connection paths. `ki configure → Aura` delegates to the `neo4j-cli` skill; `ki configure → Local` shells out to `podman` per `skills/knowledge-base/references/neo4j-podman.md` (the source of truth for container / volume / image / plugin choices). Use those — don't hand-roll either.
- Don't reach back into the parent project. This repo originated as a design-doc folder inside `create-context-graph/scratch/`. It is now independent. Don't import from or reference paths in the old location.

## Test fixtures

A deterministic Obsidian-style markdown vault generator lives at
`scripts/gen_test_vault.py`. Same `--seed` produces byte-identical output
(modulo the wall-clock timestamp in the generated `README.md`), so tests can
depend on specific filenames, wikilink targets, and frontmatter contents.

| Size     | Files  | Bytes   | Max single file | Where it lives                                            |
|----------|--------|---------|-----------------|-----------------------------------------------------------|
| `tiny`   | ~20    | ~100 KB | ~10 KB          | Committed at `tests/fixtures/sample_vault/`.              |
| `small`  | ~200   | ~10 MB  | ~100 KB         | Local iteration; not committed.                           |
| `medium` | ~2,000 | ~200 MB | ~500 KB         | Perf sanity checks; not committed.                        |
| `large`  | 10,000 | ~1 GB   | ~1 MB           | GitHub release asset, not committed (see upload script).  |

Common invocations:

```bash
# Regenerate the committed tiny fixture (e.g. after changing the generator)
uv run python scripts/gen_test_vault.py --size tiny --seed 42 \
  --output tests/fixtures/sample_vault

# Local medium vault for perf checks
uv run python scripts/gen_test_vault.py --size medium --seed 42 \
  --output ./out/vault-medium

# Build + upload the large vault as a GitHub release asset
scripts/upload_test_vault.sh                  # tag: v0.1.0-fixtures
scripts/upload_test_vault.sh v0.2.0-fixtures  # bump tag for a refresh
```

The generator enforces the size envelopes (see `docs/architecture.md` *Scalability
envelopes*) and exercises every node property / edge type in
`docs/data-model/schema.md`. Tests live at `tests/unit/test_gen_test_vault.py` —
if you change the generator, run them.

## When you're unsure

- Open `docs/scoping.md` (CLI shape + profiles/vaults/scoping) or `docs/general-philosophy.md` (tenets); `docs/README.md` indexes everything.
- For schema / Cypher questions, `docs/data-model/schema.md` is normative; `docs/data-model/ingest-cypher.md` and `docs/data-model/retrieval-queries.md` are the working queries that *match* the model.
- For agent-as-user behavior (Trigger / Do-Not-Use / On-First-Use, auto-mode rules), `skills/knowledge-base/SKILL.md` is the contract; don't change it without updating `docs/scoping.md` (and the relevant `docs/commands/` doc) in the same PR.
