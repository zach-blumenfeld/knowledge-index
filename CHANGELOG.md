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

## [Unreleased]

### Added

- **`ki search --under <uri-or-path>`** — scope a search to a containment subtree (folder / document / section). Takes a **uri** (works in either mode) or a **filesystem path** (local mode only — `-N`-safe, resolved through the on-disk marker). See [#67](https://github.com/zach-blumenfeld/knowledge-index/issues/67), `docs/commands/search.md` §5.2.
- **`ki search --profile P [--vault a,b]`** — remote mode: search a profile you're not standing in (`--profile` alone → all its vaults; `--vault` limits to a comma-separated list of vault uris; `--profile P --under <uri>` scopes to one subtree). `--vault` requires `--profile`; `--under` and `--vault` are mutually exclusive; all guards error clearly.

### Changed

- **`ki search` scope predicate** now matches a node that *is* or is *under* any of one-or-more containment roots (the `any()` three-part `= / STARTS WITH '/' / STARTS WITH '#'` test), replacing the single `uri STARTS WITH <vault>/` prefix. Correct for vault / folder / document / section roots alike. See `docs/commands/search.md` §5.4.
- **Agent skill renamed `ki` → `knowledge-base`.** The bundled skill's frontmatter `name` is now `knowledge-base`, and `ki skill install` writes it to `<agent-config>/skills/knowledge-base/SKILL.md` (previously `.../skills/ki/SKILL.md`) — a use-case-descriptive name (the skill builds and searches a *knowledge base*), sitting alongside the sibling `neo4j-cli` skill. The `ki` CLI command and the `knowledge-index` Python package are unchanged.
  - **Migration:** a prior `ki skill install` left the skill at the old `skills/ki/` path. After upgrading, re-run `ki skill install` to write the new `knowledge-base/` skill, then delete the stale `skills/ki/` directory in each agent's config by hand — `ki skill remove` now targets the new path and won't clean up the old one.

## [0.5.0] — 2026-05-22

### Added

- **`ki outline` command + `ki tree` permanent alias** ([#60](https://github.com/zach-blumenfeld/knowledge-index/issues/60)). `ki outline` is now the canonical name for the containment-tree renderer — the "key outline of this document" idiom reads naturally where `ki tree` (key + CS-data-structure jargon) didn't. `ki tree` is registered under the same callback as a hidden alias so existing skill bundles, blog posts, and muscle memory keep working; pass-through is bit-for-bit identical. This is the first of a small family of `ki <X>` verbs (`concepts` / `connections` / `references` on the roadmap) that all read like one coherent *"key X"* vocabulary.
- **Positional URI on `ki outline`** ([#60](https://github.com/zach-blumenfeld/knowledge-index/issues/60)). `ki outline <uri>` is the new canonical shape, matching `ki get <uri>`. The `--at <uri>` flag survives as a back-compat fallback (positional wins when both are passed). All four forms work: `ki outline <uri>`, `ki outline --at <uri>`, `ki tree <uri>`, `ki tree --at <uri>`.

### Changed

- **Docs sweep to `ki outline`** ([#60](https://github.com/zach-blumenfeld/knowledge-index/issues/60)). `AGENTS.md`, `docs/`, `skills/ki/SKILL.md`, and source-file docstrings now use `ki outline` as the canonical command name, with `ki tree` mentioned as a recognized alias. `docs/tree-format.md` is renamed to `docs/outline-format.md` (the format spec itself is unchanged; the file's title and every reference is retargeted). Error messages in `ki get` similarly point at `ki outline` for Folder/Vault URI hints.
- **Source-file renames to match the new vocabulary.** `src/ki/commands/tree.py` → `src/ki/commands/outline.py` (and the dispatcher `cmd_tree` → `cmd_outline`); `tests/unit/test_tree.py` → `tests/unit/test_outline.py`. The Click-callback names `outline_cmd` and `tree_cmd` are kept as-is — `tree_cmd` specifically names the alias's callback.

### Fixed

- **`ki outline --at <folder> --depth 3` no longer raises `RecursionError` on cyclic `:LINKS_TO` subgraphs** ([#60](https://github.com/zach-blumenfeld/knowledge-index/issues/60)). The DFS in `src/ki/commands/outline.py::_dfs_emit` walks the merged `HAS + LINKS_TO` child map; a section that links back to an ancestor doc (or any already-emitted node) produces a cycle in that map, which would recurse forever without a guard. Now uses a `visited: set[str]` so the walk always terminates. The `L`-row still renders so the link is visible; the renderer just won't re-expand the target's subtree under it.
- **`_parse_at` no longer truncates external URL Documents.** Pre-0.5.0 the code partitioned on the first colon to strip a `Label:uri` prefix — which silently ate the URL scheme on external URL Documents, turning `https://beltagy.net/` into `//beltagy.net/` and breaking `ki outline <url>` for any URL-keyed `:Document`. Now matches only the four real node-label prefixes (`Vault:` / `Folder:` / `Document:` / `Section:`) and returns everything else verbatim — preserving the URI-column round-trip invariant for `http://` / `https://` / `file://` URIs that came in via the #37 link-capture work.

## [0.4.1] — 2026-05-21

### Fixed

- **Friendly errors when no vaults / no search index exist** ([#57](https://github.com/zach-blumenfeld/knowledge-index/issues/57)). After `ki nuke` (or before any `ki index` on a fresh Neo4j) the two read-side verbs failed in user-hostile ways:
  - `ki search "..."` raised a raw `neo4j.exceptions.GqlError` ("There is no such fulltext schema index: `content_search`") — violating AGENTS.md principle #2. Now exits with `Error: no search index found — run \`ki index <vault>\` first to build it.`
  - `ki tree` printed `(no results)` on an empty graph. Now distinguishes "no vaults indexed yet — run \`ki index <path>\` to create one" (no `--at`) from "no node found at \`<uri>\`" (with `--at`).

## [0.4.0] — 2026-05-21

### Added

- **`.ki/vault.yaml`: vault marker now carries optional user-authored metadata** ([#21](https://github.com/zach-blumenfeld/knowledge-index/issues/21)). The marker file is YAML with a `uri:` field (ki-owned, write-once) and an optional `description:` field — a short routing hint about what this vault is for. `ki` reads the description on each `ki index` and propagates it to `Vault.description` in Neo4j (latest-write-wins). `ki` is read-only w.r.t. every field except `uri:`. AGENTS.md principle #1 and the requirements *Core design principle* are updated to reflect that the marker now carries content, not just opaque identity.
- **`ki search --type vault "..."` (B.11)** ([#21](https://github.com/zach-blumenfeld/knowledge-index/issues/21)). New retrieval mode: fulltext over `Vault.{name, displayName, description}`, intended for agents picking *which vault* to search before drilling into doc/section search scoped to that vault. Same shared fulltext index as B.1/B.2 (filtered by label).
- **`ki vault list` command** ([#21](https://github.com/zach-blumenfeld/knowledge-index/issues/21)). Lists every indexed vault under the active profile with `name`, `path`, and `description`. `--json` for machine-readable output. Emits a one-line stderr warning per vault with no description set — SKILL.md tells the agent to prompt the user for one in that case.
- **`ki index` prompts when no vault description is set** ([#21](https://github.com/zach-blumenfeld/knowledge-index/issues/21)). After the ingest summary, if `.ki/vault.yaml` has no `description:`, ki prints a one-line yellow hint with the marker path and a YAML stub — most natural moment to act on it.
- **`ki index --description "..."` flag** ([#29](https://github.com/zach-blumenfeld/knowledge-index/issues/29)). Sets `description:` in `.ki/vault.yaml` before ingesting so the value propagates to `Vault.description` in a single command. Refuses to overwrite an existing description; pass `--force-description` to replace. Additionally, the first `ki index` on a fresh vault (no marker yet) now prompts for a description interactively when stdin is a TTY and neither `--description` nor `--yes` was passed. Empty input falls through to the existing post-ingest warning. Both paths honour the 8 KB cap.
- **`Vault.description` schema property.** Optional string, soft-capped at ~8 KB (truncated with a warning if longer), no MERGE-key impact. See `docs/data-model.md` §Vault.
- **`:Folder` node + single `HAS` containment edge** ([#17](https://github.com/zach-blumenfeld/knowledge-index/issues/17)). The graph now materialises one `:Folder` per distinct directory containing at least one indexed `:Document` — empty directories never appear. `Folder.uri = <vaultId>/<slugified directory path>` (a strict prefix of every Document URI under it). Properties are intentionally minimal: `uri`, `name`, `displayName`, `firstSeenAt`, `lastSeenAt`. No `description`, no `aliases`, no fulltext indexing — folders are a navigation surface, not a retrieval surface. The vault hierarchy is now a true tree: each Folder / Document / Section has exactly one incoming `HAS` edge from its immediate parent. The previous separate `HAS_FOLDER` / `HAS_DOCUMENT` / `HAS_SECTION` edge types are collapsed into a single `HAS` because they all expressed the same semantic ("parent in the containment tree") — see `docs/data-model.md` §4.2 *Why one relationship type instead of three* for the rationale.
- **`ki tree` command** ([#17](https://github.com/zach-blumenfeld/knowledge-index/issues/17)). New third top-level verb. Renders the containment hierarchy (`HAS`) plus outbound `LINKS_TO` branches as a ToC-style table (NAME | T | URI). `--at <uri>` scopes to a subtree, `--depth N` bounds traversal, `--full` expands further. Sections sort by `NEXT_SECTION` position; folder/document siblings sort alphabetically. No `--at` → every vault becomes a root. URI column always shows the full URI so output is copy-pasteable into `ki tree --at <uri>` or `ki get <uri>`. Backed by new B.12 / B.12-links queries in `docs/retrieval-queries.md`.
- **`ki get <uri> [<uri> ...]` command** ([#33](https://github.com/zach-blumenfeld/knowledge-index/issues/33)). Closes the search/tree → URI → fetch loop. Accepts `:Document` / `:Section` URIs; rejects `:Folder` / `:Vault` with hints pointing at `ki tree` / `ki vault list`. `--type content` (default) returns the node's stored content; `--type full` reconstructs the reading-order body via B.4 (Document) or B.14 (Section subtree); `--type path` returns the metadata shell only.
- **`path` property on `:Folder`, `:Document`, `:Section`** ([#40](https://github.com/zach-blumenfeld/knowledge-index/issues/40)). Absolute POSIX path on the ingesting machine. Lets agents jump from any `ki` URI to the on-disk file or directory in one shot — no more `Vault.path` + URI-prefix-strip + concat. Every result row from `ki search` / `ki get` / `ki tree` is now self-sufficient for `Read /path/to/file`. `Section.path` mirrors the owning Document's path. Machine-scoped, same caveat as `Vault.path`.
- **Capture all markdown links as `:Document` nodes** ([#37](https://github.com/zach-blumenfeld/knowledge-index/issues/37)). The parser now classifies every `[text](href)` into one of four kinds and routes accordingly: `wikilink` (unchanged), `md_link` (unchanged), `non_md_file` (internal stub `:Document` with `sourceType=LOCAL_FILE`, `path`, `fileHash`, `HAS`-attached), and `external_url` (`:Document` with `sourceType=URL_LINK`, no path / no `HAS`). Vault-escaping `../` paths become external `file://` Documents. Same URL referenced from two vaults collapses into one node with `LINKS_TO` from both. Link text populates `displayName` (first link wins) and the target's `aliases` for stubs / externals. `ki search --types document` and `ki tree` surface these.
- **`ki nuke` command** ([#3](https://github.com/zach-blumenfeld/knowledge-index/issues/3)). Resets the entire graph: batched `DETACH DELETE` of every node, drops all ki-owned constraints and the fulltext index, removes every `.ki/vault.yaml` ki knows about. Typed "nuke" confirmation required. `--keep-marker` preserves markers for rebuild-onto-same-uri. Batched via `CALL ... IN TRANSACTIONS OF $chunkSize ROWS` to avoid JVM-heap OOM on large graphs.
- **Vault-level sync on re-ingest** ([#3](https://github.com/zach-blumenfeld/knowledge-index/issues/3)). `ki index <existing-vault>` now nukes the vault's contents before re-ingest via the shared removal routine — files removed from disk between ingests naturally vanish from the graph; the `fileHash`-skip optimization gracefully no-ops post-nuke. Orphan external `:Document` GC runs after vault removal (snapshot-and-recheck for degree-zero nodes). See `docs/index_rm_behavior.md`.
- **`Vault.uri` is now a human-readable slug** ([#33](https://github.com/zach-blumenfeld/knowledge-index/issues/33)). Derived from the vault directory's basename (e.g. `~/my-notes` → `"my-notes"`). On collision with an existing vault in the same Neo4j, appends `-N`. Document / Section / Folder URIs gain the slug as their prefix instead of a UUID — the entire URI schema is now readable and quotable. Trade-off: deleting a vault frees its slug for reassignment.
- **`ki search` redesign** ([#33](https://github.com/zach-blumenfeld/knowledge-index/issues/33)). Default behavior runs all three types (vault + document + section), tags each row with its label, sorts by fulltext score, and caps at `--k` total. New `--types` filter (replaces single-shape `--type {section,document,vault}`). Plain-text output uses one unified table (`score | T | displayName | uri`) matching `ki tree`'s `Key:`-header convention. `--json` keeps native B.1 / B.2 / B.11 row shapes plus a `label` field.
- **`ki configure → Local` now uses Podman** ([#11](https://github.com/zach-blumenfeld/knowledge-index/issues/11)). Replaces the previous neo4j-local desktop-app dependency with a self-managed `neo4j:latest` container (APOC + GenAI plugins, heap 1G + pagecache 512M). Profile source = `local-podman`. Includes a Bolt-readiness probe (TCP gate + `cypher-shell RETURN 1`) so the driver doesn't race the port-open event. `references/neo4j-podman.md` is the canonical agent-followable runbook.
- **3-phase ingest progress bar** ([#53](https://github.com/zach-blumenfeld/knowledge-index/issues/53)). Reading / processing / finalizing phases each get their own progress line, with running added/updated/skipped counts on the processing bar. Rich-backed implementation on TTY runs; silent otherwise.
- **Typed `IngestServiceUnavailable` error** ([#54 partial](https://github.com/zach-blumenfeld/knowledge-index/issues/54)). When Neo4j drops the Bolt connection mid-ingest (typically JVM OOM), `ki index` now surfaces a focused error with profile-aware recovery hints (canonical `neo4j-ki` container commands for `local-podman`; generic `--batch-size` / heap / vault-split guidance for `aura` / `existing`) instead of an 80-line driver traceback.

### Changed

- **Fulltext index renamed `doc_section_search` → `content_search`** and expanded to cover `:Vault` over `[displayName, content, aliases, description]`. Neo4j fulltext silently skips missing properties per label, so the same index serves `:Document`, `:Section`, and `:Vault` cleanly.
- **Containment edges collapsed to a single `HAS` relationship type** ([#17](https://github.com/zach-blumenfeld/knowledge-index/issues/17)). Pre-0.4.0 the graph had `HAS_DOCUMENT` (Vault → Document) and `HAS_SECTION` (Document/Section → Section). Both are renamed to `:HAS`, and `:Folder` is wired into the same edge type (`Vault|Folder -[:HAS]-> Folder|Document` for the folder/doc tree; `Document|Section -[:HAS]-> Section` for the section tree). All B.queries that walked `HAS_SECTION` / `HAS_DOCUMENT` updated to walk `:HAS*` — retrieval shape is unchanged. Endpoint constraints (which parent-label pairs with which child-label) are enforced at ingest, not by Neo4j's relationship-type system.
- **`ki rm` is now vault-only** ([#3](https://github.com/zach-blumenfeld/knowledge-index/issues/3)). The document / subtree / vault `ki rm` model is gone. File paths and subdirectories error with a message pointing at `ki index` (vault-level sync, above) as the right tool for sub-vault cleanup.
- **Streaming file reads during ingest** ([#54 partial](https://github.com/zach-blumenfeld/knowledge-index/issues/54)). The previous slurp-all read held every file's bytes resident for the full ingest run — for a 1 GB vault, 1 GB of Python RAM throughout. Reads now stream in batches of 64 (concurrent within a batch, semaphore-bounded), and each batch's bytes drop out of scope before the next is read. Peak RSS is now O(batch × avg_file_size), not O(vault_size).
- **Neo4j container memory right-sized** ([#54 partial](https://github.com/zach-blumenfeld/knowledge-index/issues/54)). The Podman `Local` profile now bakes `NEO4J_server_memory_heap_initial__size=1G` + `pagecache=512M` into the canonical `podman run`. ~2 GB total Neo4j footprint fits a personal laptop and leaves headroom. Preflight recommends `podman machine init --memory 4096 --cpus 4`. The batcher's existing OOM auto-recovery (halve-and-retry, floor 16) absorbs occasional fat transactions.

### Fixed

- **`Document.displayName` now consistently equals the filename**, never the first H1 ([#28](https://github.com/zach-blumenfeld/knowledge-index/issues/28)). Pre-#28 the ingest pipeline silently promoted the first H1 heading to `displayName` when present, drifting from the documented behavior in `docs/data-model.md`. Re-index any existing vault to refresh; B.1 / `--type document` queries still match the H1 text via `content_search`'s `content` + `aliases` coverage, so retrieval recall is unchanged.
- **`ki search --type neighbors` no longer fails with `Invalid input '$': expected '}' or an integer value`.** Neo4j 5.x (including current Aura) rejects Cypher parameters inside a quantified-path-pattern quantifier, so `run_b3` now substitutes the depth literal into the query string client-side (safe: the int is coerced first). B.12's `{1,$depth}` quantifier carries the same limitation and is handled the same way. New integration test guards against regression. (`--type neighbors` itself was subsequently dropped — see Breaking.)
- **Tolerate malformed YAML frontmatter** ([#53](https://github.com/zach-blumenfeld/knowledge-index/issues/53)). Frontmatter parsing now recovers from PyYAML errors via sanitize-and-retry (strips ASCII control chars) and falls back to empty fields with a logged warning if even sanitized YAML won't parse. Surfaced during ingest of an arxiv-markdown vault where ~29 docs carried `0x7F` in summary text and aborted the whole run.

### Breaking

- **`.ki/vault-id` (bare-UUID marker) is no longer read.** The pre-0.4.0 single-line UUID format has been dropped with no auto-migration; vaults indexed with prior versions need to be **wiped + re-indexed** after upgrading. Existing Neo4j databases also still carry the old `doc_section_search` fulltext index and orphan `HAS_SECTION` / `HAS_DOCUMENT` edges as dead objects — drop them manually if desired (`DROP INDEX doc_section_search; MATCH ()-[r:HAS_SECTION|HAS_DOCUMENT]->() DELETE r`), or just `ki nuke` and re-ingest. No active users when this shipped; we traded migration code for simplicity.
- **`ki rm` accepts only a vault now** ([#3](https://github.com/zach-blumenfeld/knowledge-index/issues/3)). The previous `--doc` / `--subtree` flags are gone. Pre-0.4.0 incantations like `ki rm <vault>/notes/foo.md` will error; use `ki index <vault>` to re-sync after deleting source files.
- **`ki search --type neighbors` and `--doc-uri` removed** ([#33](https://github.com/zach-blumenfeld/knowledge-index/issues/33)). The neighbors mode was effectively unused. The backlinks gap is tracked in [#35](https://github.com/zach-blumenfeld/knowledge-index/issues/35); `--under` scoping in [#36](https://github.com/zach-blumenfeld/knowledge-index/issues/36).
- **Re-index required.** Schema additions (`:Folder`, `path` on F/D/S, link-capture `:Document` kinds, slugged URIs, single `HAS` edge type) are not back-compatible with pre-0.4.0 indices. v0.x; no commitment to migration code.

### Deferred to a later release

- **Obsidian URL scheme** ([#22](https://github.com/zach-blumenfeld/knowledge-index/issues/22)) — inbound `obsidian://` and vault-root-relative link parsing. Outbound clickability is already covered by `path` (#40); the parser gap only bites vaults that contain those link forms.
- **Getting Started vault-construction guide** ([#23](https://github.com/zach-blumenfeld/knowledge-index/issues/23)) — covered more concretely by the [llm-kb-graph](https://github.com/zach-blumenfeld/llm-kb-graph) worked example.

## [0.3.1] — 2026-05-14

### Fixed

- **Integration test `test_section_target_display_text_aliases_the_section`
  no longer fails when run live.** The fixture wrote `Darth Vader.md` with
  an `# Darth Vader` H1, which makes the `## Origins` section's URI
  `<doc>#darth-vader/origins` (heading path includes the H1 ancestor). The
  Obsidian-style wikilink `[[Darth Vader#Origins|…]]` only encodes the
  bare heading text, so the resolver computed `<doc>#origins` and the link
  never landed on the real section — which meant v0.3.0's display-text
  alias step had nothing to alias. Dropped the H1 from the fixture so the
  section URI matches what the resolver computes. This is a test fixture
  fix only; the v0.3.0 alias code is unchanged. The underlying resolver
  gap (`[[Doc#Heading]]` against sections nested under an H1) is
  pre-existing and out of scope for this release.

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
