# <img src="img/ki.png" alt="ki logo" width="120" align="left" />  Knowledge Index (`ki`)
> The safe, easy, fast search engine for your knowledge base.
<br clear="left">

Point `ki` at a folder of markdown — notes, docs, a wiki — and it syncs to a Neo4j knowledge-graph index you can search and navigate in seconds, from the CLI or any AI agent. Agents and humans alike go **understand → search → retrieve** with no prior expertise.

**`ki` is a search index, not a store.** It only reflects what's on your filesystem — **source files are never modified** (safe on an Obsidian vault, a git repo, a research folder), the index is disposable cache you can rebuild with one `ki index`, and there's **no LLM work or embeddings at index time** (vendor-neutral, instant to set up, free). See [`docs/general-philosophy.md`](docs/general-philosophy.md) for the tenets.

## Install

```bash
curl -sSfL https://knowledge-index.ai/install.sh | bash   # ki + neo4j-cli + agent skills
ki configure                                              # one-time Neo4j: Local (Podman), Aura, or Existing
```

Prefer Python tooling? `uv tool install knowledge-index` (or `uvx knowledge-index --help` to try without installing).

## Quickstart

```bash
cd ~/my-notes
ki index . --profile personal     # sync the folder into the graph (first index binds a profile)
ki outline my-notes --full        # table-of-contents view of the vault
ki search "rate limiting"         # find the right slice
ki get --type full "<uri>"        # read it (copy a uri from outline/search)
```

`ki` writes one marker per vault — `.ki/vault.yaml`, holding the vault's slug `uri`, its bound profile name, and an optional `description`. It's safe to commit (no credentials). Re-running `ki index` resyncs; nuke the graph and rebuild anytime.

## With an AI agent

Coding agents (Claude Code, Cursor, Windsurf, …) shell out to `ki` directly. `ki skill install` drops the bundled **`knowledge-base`** skill into each detected agent's config so it knows *when* to reach for `ki` — *"what did I write about X?"*, *"build a knowledge base from this folder,"* *"find related material"* — and when not to.

```bash
ki skill install      # into all detected agents (the installer already does this)
ki skill list         # what's wired up / detected
```

The shipped skill scopes to the **local, single-vault** workflow; see [`docs/skills.md`](docs/skills.md).

## Usage patterns

`ki` is optimized for **one local vault at a time** and generalizes from there. The full model — profiles, vaults, and how every command resolves scope — is [`docs/scoping.md`](docs/scoping.md).

1. **Local, single vault** *(the default, ~95%)* — your markdown on disk; the full lifecycle (index/update + search/retrieve). No flags: `ki` uses the vault you're standing in.
2. **Remote vaults** — read-only search/retrieval over a KB you *don't* have locally (sharing a KB, powering apps/production). Name it explicitly with `--profile` (+ `--vault`).
3. **Cross-vault search** — query several vaults in one profile at once (`--profile`; navigation/read commands only).

## Commands

`ki <cmd> --help` is the source of truth for flags.

- **Index / update:** `index` (full sync) — `add` / `rm` / `mv` (incremental) are planned.
- **Read / retrieve:** `search`, `outline` (alias `tree`), `get`, `status`. Depth docs: [search](docs/commands/search.md) · [get](docs/commands/get.md) · [outline](docs/commands/outline.md).
- **Remove from index:** `drop` (one vault) · `nuke` (a whole profile). Source files are never touched.
- **Admin / info:** `configure`, `profile list`, `vault list`, `skill`.

`ki search` is **one ranked fulltext sweep over documents + sections** (no embeddings) — narrow with `--types`, scope locally with `--under` or remotely with `--profile` / `--vault`. See [`docs/commands/search.md`](docs/commands/search.md).

## Neo4j connection

`ki configure` offers three paths: **Local** (Podman `neo4j:latest` + APOC/GenAI — full runbook in [`skills/knowledge-base/references/neo4j-podman.md`](skills/knowledge-base/references/neo4j-podman.md)), **Aura** (billable cloud via `neo4j-cli`; never picked without consent), and **Existing** (any Bolt URI). Credentials live in `~/.config/ki/config.yaml` (mode `0600`); vaults reference profiles by **name**, so syncing a vault folder never leaks them.

## Limitations & roadmap

- **Markdown only** — convert PDF / docx / HTML first (`pandoc`, `markitdown`), then `ki index`.
- **No vector search yet** — fulltext is the substrate (by design — see the philosophy); hybrid is roadmap.
- **No MCP server yet** — coding agents work today; chat apps (claude.ai, ChatGPT, …) need an MCP bridge (roadmap).
- **Plaintext passwords** (`config.yaml`, mode `0600`) — OS keyring is roadmap.

Full roadmap → [open issues](https://github.com/zach-blumenfeld/knowledge-index/issues).

## Docs

Start at [`docs/`](docs/README.md): [architecture](docs/architecture.md) · [general-philosophy](docs/general-philosophy.md) · [scoping](docs/scoping.md) · [data model](docs/data-model/schema.md). Contributors: [`AGENTS.md`](AGENTS.md).

## Development

```bash
git clone https://github.com/zach-blumenfeld/knowledge-index.git && cd knowledge-index
uv sync --extra dev
uv run pytest                                   # integration tests auto-skip without a Neo4j
uv run ruff check src/ tests/ scripts/
```

Point integration tests at any Neo4j via `KI_TEST_NEO4J_URI` / `KI_TEST_NEO4J_USER` / `KI_TEST_NEO4J_PASSWORD` (the suite is destructive — don't use a Neo4j with real data). Design principles, project map, test fixtures, and the release flow live in [`AGENTS.md`](AGENTS.md).

## License

See [`LICENSE`](LICENSE).
