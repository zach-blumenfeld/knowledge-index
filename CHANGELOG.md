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
  large) matching the §Scalability envelopes in `docs/requirements.md`.

### Known limitations

- v1 indexes `.md` files only. Convert non-markdown sources to markdown first
  (see `skills/ki/SKILL.md` *PREPARE when*).
- Vector indexes / embeddings are deferred; fulltext is the retrieval substrate.
- Retrieval queries B.4–B.10 from `docs/retrieval-queries.md` are not yet
  wired into the CLI.
