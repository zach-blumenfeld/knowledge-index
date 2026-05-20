# AGENTS.md

Instructions for AI agents (Claude, Codex, Cursor, etc.) working **on** the `knowledge-index` codebase. If you are an agent being asked by a user to *use* `ki` (index their notes, search their knowledge base, etc.), read `skills/ki/SKILL.md` instead.

## What this repo is

`knowledge-index` (CLI: `ki`) is a personal knowledge index backed by Neo4j. It reads a local folder of markdown files and maintains a searchable knowledge graph over them. The two primitive verbs are `ki index` (sync) and `ki search` (query); navigation/management commands (`ki vault list`, `ki rm`, `ki configure`, `ki init`, and the v0.4.0 `ki tree`) sit on top of those. For the full design spec see `docs/requirements_v01_mvp.md`.

## Non-negotiable design principles

These constrain every change you make. If a proposed feature violates one of these, reject the proposal rather than working around it.

1. **`ki` is an index, not a document store.** Never mutate user-owned source files (`.md`). All `ki` output lives in `~/.config/ki/` (config), Neo4j (the index), or `.ki/vault.yaml` (vault identity + optional user-authored description per vault). `ki` writes the `uri:` field on first creation and otherwise touches user-authored fields **only when the user explicitly asks** via a flag (`ki index --description "..."` writes `description:`; without the flag, `ki` is read-only). No `--purge` flag, no "auto-organize my notes," no rewriting frontmatter. See `docs/requirements_v01_mvp.md` *Core design principle* for the long form.
2. **The backend is opaque to the user.** From the user's and the agent-as-user's perspective, `ki` is a search tool. They don't need to know about Cypher, Neo4j, or graph traversal. Don't surface backend concepts (Cypher errors, node labels, etc.) in default output.
3. **One source of truth per concern.** Config lives at `~/.config/ki/config.yaml`; vault identity + per-vault user metadata live in `.ki/vault.yaml`; graph data lives in Neo4j. Don't introduce parallel state.
4. **Safe by default, dangerous by flag.** Destructive operations (whole-vault removal) require explicit flags AND typed confirmation. Cloud-resource creation (Aura) requires explicit consent even on agent auto-mode. See `docs/requirements_v01_mvp.md` *Agent auto-mode behavior*.

## Project map

| Path                              | What's there                                                                                                                                                            |
|-----------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `src/ki/cli.py`                   | Click entry point. Wires up the top-level commands (`configure`, `index`, `search`, `rm`, `init`) and the subcommand groups (`vault list`, `skill {list,install,remove,print}`). Each command lives in its own module under `src/ki/commands/`. |
| `src/ki/config.py`                | XDG-aware config loader; named profiles; 0600 mode on write; `KI_PROFILE` env-var override.                                                                             |
| `src/ki/vault.py`                 | `.ki/vault.yaml` marker IO (UUID + user-authored description), slug rules, Document/Section URI construction.                                                           |
| `src/ki/parser/markdown.py`       | markdown-it-py-based parser. Builds section tree per *Content Construction Rules* (Rule 1–3) and exposes a DFS-ordered flat list for `NEXT_SECTION`.                    |
| `src/ki/parser/frontmatter.py`    | python-frontmatter wrapper. Splits YAML frontmatter into `aliases`, `frontmatterCreatedAt`, and a JSON blob of unknown keys.                                            |
| `src/ki/ingest/pipeline.py`       | Per-vault orchestrator: schema, per-vault upsert, fileHash skip, concurrent reads (aiofiles), single Neo4j write session, one doc at a time, LINKS_TO post-pass.        |
| `src/ki/ingest/batcher.py`        | UNWIND batching + Neo4j-OOM auto-recovery (halve and retry once, continue smaller).                                                                                     |
| `src/ki/ingest/queries.py`        | Cypher lifted verbatim from `docs/ingest-cypher.md`.                                                                                                                    |
| `src/ki/ingest/provenance.py`     | Builds the `User` mutable bag and `LOADED` provenance bag (best-effort detection per `docs/data-model.md`).                                                             |
| `src/ki/search/queries.py`        | B.1 (document title) / B.2 (section content) / B.3 (neighbourhood) / B.11 (vault fulltext) from `docs/retrieval-queries.md`. B.12 (containment tree) lands with `ki tree` (#17 phase 3). |
| `src/ki/neo4j_client.py`          | Driver lifecycle, `ensure_schema`, `verify_connectivity`.                                                                                                               |
| `src/ki/neo4j_local.py`           | Thin wrapper around the `neo4j-local` CLI; used by `ki configure → Local` and the integration test fixture.                                                             |
| `tests/unit/`                     | Pure-Python unit tests — parser, slug, vault marker, config, batcher (mocked driver), CLI parsing.                                                                      |
| `tests/integration/`              | End-to-end tests against an ephemeral Neo4j. Auto-skip if neither `neo4j-local` is installed nor `KI_TEST_NEO4J_*` env vars are set.                                    |
| `tests/fixtures/sample_vault/`    | ~20-doc deterministic tiny vault covering every node property + edge type. Generated by `scripts/gen_test_vault.py --size tiny --seed 42`. Do not hand-edit — regenerate. |
| `scripts/gen_test_vault.py`       | Deterministic Obsidian-style markdown vault generator. Four sizes (`tiny` / `small` / `medium` / `large`) matching the §Scalability envelopes. See *Test fixtures* below. |
| `docs/requirements_v01_mvp.md`            | Full design spec. Read this before making non-trivial changes — name, CLI shape, configuration model, auto-mode rules, all live here.                                   |
| `docs/data-model.md`              | Neo4j schema: `User`, `Vault`, `Folder`, `Document`, `Section` node properties; `USES_VAULT`, `LOADED`, `HAS`, `LINKS_TO` edges. |
| `docs/ingest-cypher.md`           | Batched `UNWIND` ingest queries (§4.3) and constraints / fulltext index (§4.4). Modify here when changing what `ki index` writes to Neo4j.                              |
| `docs/retrieval-queries.md`       | Retrieval queries `B.1`–`B.12` (fulltext search incl. vault routing, neighbourhood, document text, windowing, backlinks, shortest path, containment-tree walk). Modify here when changing what `ki search` / `ki vault list` / `ki tree` expose. |
| `skills/ki/SKILL.md`              | Agent-as-user routing rules (TRIGGER / PREPARE / SKIP). Ships with the published tool.                                                                                  |
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
- **Cypher lives in `.md` files under `docs/`, not in `.cypher` files or string literals.** When implementation lands, `src/ki/queries/` should parse Cypher *out of* `docs/*.md` so the docs are the source of truth — no drift between spec and code.
- **One `uri` MERGE key per node label.** Composite keys (the old `{userId, vaultId, name}` pattern) are explicitly rejected. See `docs/data-model.md`.
- **Batched ingest via `UNWIND $rows AS row`.** Single-row MERGE is ~10–100× slower against Neo4j; always batch.
- **`LOADED` provenance props (`agentName`, `agentVersion`, `os`, `hostname`, ...) are lifted out of `UNWIND`** into a top-level `$loadProps` map. Don't duplicate them per row.

## Don't

- Don't add vector indexes in v1. Embeddings are deferred; fulltext is the retrieval substrate. The `genai` plugin in `neo4j-local` is loaded for the upgrade path but unused.
- Don't bake conversion logic (PDF→markdown, docx→markdown) into `ki`. If the agent-as-user needs that, *the agent* does the conversion; `ki` only indexes whatever `.md` files exist. See `docs/requirements_v01_mvp.md` *Prepare when* clause.
- Don't write a `curl | sh` installer for v1. `uvx knowledge-index` is the install path. Standalone binaries are post-v1 if real demand appears.
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

The generator enforces the size envelopes documented in `docs/requirements_v01_mvp.md`
§Scalability and exercises every node property / edge type in
`docs/data-model.md`. Tests live at `tests/unit/test_gen_test_vault.py` —
if you change the generator, run them.

## When you're unsure

- Open `docs/requirements_v01_mvp.md` and search for the keyword. The full design intent is captured there.
- For schema / Cypher questions, `docs/data-model.md` is normative; `docs/ingest-cypher.md` and `docs/retrieval-queries.md` are the working queries that *match* the model.
- For agent-as-user behavior (TRIGGER / PREPARE / SKIP, auto-mode rules), `skills/ki/SKILL.md` is the contract; don't change it without updating `docs/requirements_v01_mvp.md` in the same PR.
