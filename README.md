# Knowledge Index (`ki`)
> Search index for agent memory — knowledge graph index for your documents

One searchable graph index across all your documents — point `ki` at a folder (or many) and query the result from the CLI or any AI agent. Multiple folders and users can share the same index. Source files are never modified, so it's safe on an Obsidian vault, a git repo, or a research folder. Backed by Neo4j.

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

## Quick start

```bash
ki configure                    # one-time: pick local Neo4j, Aura, or an existing instance
ki index ./my-vault             # sync the folder into the graph (idempotent)
ki search "what did I write about retrieval?"

ki rm ./my-vault/notes/old.md   # remove a doc from the index (source file untouched)
```

## What ki indexes

v1 indexes `.md` files only. For PDFs, docx, HTML, or plaintext, convert to markdown first (with `pandoc`, `markitdown`, or by reading + transcribing) and then run `ki index` on the output folder — see the *PREPARE when* section of [`skills/ki/SKILL.md`](skills/ki/SKILL.md). Native ingest of other formats is on the roadmap, not in v1.

## Learn more

- [`docs/requirements.md`](docs/requirements.md) — full design spec (CLI shape, schema, scalability, auto-mode rules)
- [`AGENTS.md`](AGENTS.md) — for AI agents contributing to the codebase
- [`skills/ki/SKILL.md`](skills/ki/SKILL.md) — agent routing rules (when an agent should invoke `ki`)

## License

See [`LICENSE`](LICENSE).
