# Changelog

All notable changes to `knowledge-index` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!--
HEADING FORMAT IS LOAD-BEARING.
The release workflow (.github/workflows/release.yml) extracts the body
of the GitHub Release by awk-matching `## [X.Y.Z]` at the start of a
line, up to the next `## [` heading. Keep version sections under that
exact pattern. Editorial prose is fine; just don't change the heading.
-->

## [0.3.0] — 2026-05-14

### Added

- **Wikilink display-text → target aliases.** When the parser sees a piped
  wikilink (`[[Darth Vader|Anakin]]` or `[[Darth Vader#Origins|Anakin]]`),
  the display text now propagates to the *target's* `aliases` list at
  ingest time. The existing `doc_section_search` fulltext index already
  covers `aliases`, so `ki search "Anakin"` starts matching the Darth
  Vader document without any retrieval-query changes. Display texts are
  normalized (trimmed, length-thresholded, stopword-filtered, deduped
  case-insensitively, capped at 50 per target) and unioned with — never
  overwriting — any frontmatter aliases the user authored.
- **Skill: query-expansion pattern.** `skills/ki/SKILL.md` now documents
  a "Query expansion for semantic equivalence" pattern for the calling
  LLM — when top-`k` looks weak, retry with plausible alternates from
  world knowledge (e.g. "JFK" → "John F Kennedy", "Kennedy"). Covers the
  long tail the ingest-side alias path can't reach.

### Changed

- **Schema:** `Section.aliases` (list[string], optional) added for parity
  with `Document.aliases`, so wikilinks that target sections
  (`[[Doc#Heading|Display]]`) feed the section's alias list. The fulltext
  index already declared `aliases` on both labels — no DDL change needed.
- **Docs:** stale `docs/requirements.md` path references updated to
  `docs/requirements_v01_mvp.md` across `AGENTS.md`, `CLAUDE.md`,
  `README.md`, `CHANGELOG.md`, `skills/ki/SKILL.md`, and module docstrings
  under `src/ki/` and `scripts/`. The renamed v0.1 design spec is still
  load-bearing for everything not changed by this release.
- **Docs:** new `docs/ingest-cypher.md` §4.3 step 7 (display-text
  aggregation) documents the post-`LINKS_TO` write that unions normalized
  display texts into target aliases.

### Fixed

- **Version sources are in sync again.** `pyproject.toml` and
  `src/ki/__init__.py` both report `0.3.0`; the prior release shipped
  with the latter still at `0.1.0`. A new
  `tests/unit/test_version_in_sync.py` makes future drift a test
  failure.

## [0.2.0] — 2026-05-14

### Added

- **`ki skill` command group** for installing the bundled agent routing
  rules (`skills/ki/SKILL.md`) into the right config path for each
  supported AI agent — no more hand-copying or `curl`-ing the file.
  - `ki skill list` — show the supported-agent catalog, which agents are
    detected on the machine, and which already have the skill installed.
  - `ki skill install [agent]` — install into one agent (case-insensitive
    name lookup), or into every *detected* agent if no name is given.
  - `ki skill install [agent] --path <FILE>` — escape hatch for agents
    not in the catalog (or non-standard install locations).
  - `ki skill remove [agent]` — idempotent removal; cleans up the per-tool
    directory when empty.
  - `ki skill print` — write the bundled SKILL.md to stdout.
- Supported-agent catalog mirrors
  [`neo4j-cli skill`](https://github.com/neo4j-labs/neo4j-cli) so users
  have one mental model across both tools: `claude-code, cursor, windsurf,
  copilot, gemini-cli, cline, codex, pi, opencode, junie`. `$XDG_CONFIG_HOME`
  resolution supported (used by `opencode`).
- README restructured into three audience-shaped Getting Started subsections
  (coding agent / direct CLI / chat app) plus a named-deferrals Roadmap
  section (local-Neo4j wrapper not ready, fulltext-only retrieval,
  markdown-only ingest, no MCP server) and a Development section
  (setup / tests / lint / fixtures / contributing / release flow).

### Changed

- `skills/ki/SKILL.md` synced with the actual v0.2 CLI:
  - Replaced the stale "two working commands" line with the real
    five-command surface (`configure / index / search / rm / init`) plus
    a pointer to `ki skill`.
  - New **Picking a search mode** subsection — table mapping user intent
    to `--type {section|document|neighbors}` so agents pick the right
    retrieval shape instead of always defaulting to section search.
  - Auto-mode Neo4j-picking guidance updated — no more "default to Local"
    (which depends on the unpublished `neo4j-local` binary). Ordered
    fallback: reachable existing Neo4j → ask the user. Never pick Aura
    silently.
  - New **Capabilities not yet wired** section names B.4, B.7/B.8, B.9,
    B.10, vector search, and chat-app integration so agents don't promise
    features `ki` can't deliver in v1.

### Packaging

- Wheel now bundles `skills/ki/SKILL.md` at `ki/_resources/SKILL.md`
  (hatchling `force-include`) so `ki skill install` works from a
  `uv tool install knowledge-index` without any external download.
  Dev/editable checkouts fall back to the canonical repo path.

## [0.1.0] — 2026-05-13

Initial release.

### Added

- Five-command CLI: `ki configure`, `ki index`, `ki search`, `ki rm`, `ki init`.
- Markdown ingest pipeline backed by Neo4j. Single write session, one
  document at a time end-to-end, bounded concurrent file reads (default 16),
  `fileHash` skip for unchanged files, per-file size guard (default 10 MB),
  Neo4j-OOM auto-recovery (halve the batch, retry, continue smaller).
- Section tree per `docs/data-model.md` Content Construction Rules: shallow
  content with `uri:` child pointers, skipped heading levels kept as direct
  children, duplicate-heading disambiguation scoped per parent.
- `NEXT_SECTION` chain in DFS reading order, rebuilt per ingest.
- Wikilink + markdown-link extraction, alias-aware resolution against
  Document `name` + frontmatter `aliases`.
- Fulltext retrieval via `doc_section_search` index:
  - `--type document` (B.1 — document title)
  - `--type section` (B.2 — section content; default)
  - `--type neighbors` (B.3 — `LINKS_TO` neighbourhood)
- Removal with blast-radius-scaled safety: single doc no prompt, subtree
  prompts with count, `--vault` requires typed display-name confirmation,
  `--dry-run`, `--yes`, `--keep-marker`. There is intentionally no `--purge`.
- Named profiles in `~/.config/ki/config.yaml` (XDG-first, file mode `0600`,
  `KI_PROFILE` env-var override).
- Deterministic test-vault generator (`scripts/gen_test_vault.py`) producing
  byte-identical Obsidian-style vaults at four sizes (tiny / small / medium /
  large) matching the §Scalability envelopes in `docs/requirements_v01_mvp.md`.

### Known limitations

- v1 indexes `.md` files only. Convert non-markdown sources to markdown first
  (see `skills/ki/SKILL.md` *PREPARE when*).
- Vector indexes / embeddings are deferred; fulltext is the retrieval substrate.
- Retrieval queries B.4–B.10 from `docs/retrieval-queries.md` are not yet
  wired into the CLI.
