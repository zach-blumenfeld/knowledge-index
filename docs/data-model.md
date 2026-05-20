
### Nodes

#### `User`

Driven by **shareable identity + machine + agent/model metadata** Graph Vault can detect at ingest or MCP-connect time. Everything except `id`, `createdAt`, `lastSeenAt` is best-effort: detected automatically, set to `null` if unavailable, and overridable by flag. No interactive prompts.

| Property            | Type | Required | Description                                                                                                              |
|---------------------|---|---|--------------------------------------------------------------------------------------------------------------------------|
| `id`                | string | yes | PK. Stable identifier. MERGE key. Get from  `--user` flag → `$USER` env → `getpass.getuser()`.                           |
| `displayName`       | string | no | `--user-name` → `git config user.name`. Human-readable name; shareable.                                                  |
| `email`             | string | no | `--user-email` → `git config user.email`. Useful for multi-machine dedup of the "same" person. Shareable.                |
| `firstSeenAt`       | datetime | yes | First-seen. ON CREATE only.                                                                                              |
| `lastSeenAt`        | datetime | yes | Touched on every ingest.                                                                                                 |
 

> **Provenance philosophy.** Everything in this table is information the user has *already shared* with their git config, their OS, or their agent. Nothing here is more sensitive than `~/.gitconfig`. This is the line: detect freely, never prompt, always honour an explicit flag override.

#### `Vault`
Name inspired by an [Obsidian vault](https://obsidian.md/help/vault) it represents a folder on a file systmem.

Vault identity is carried by a `.ki/vault.yaml` marker file written into the vault root on first ingest (same trick as `.git/`, `.obsidian/`, JetBrains `.idea/`). The marker travels with the folder, so a vault synced across machines via Dropbox / iCloud / git resolves to the **same** `:Vault` node — independent of user, independent of machine. Multiple users can therefore `USES_VAULT` the same vault. The file also accepts a user-authored `description:` field — a short routing hint about what this vault is for — which `ki` reads on each ingest and propagates to `Vault.description`. `ki` writes `uri:` on first creation and is read-only w.r.t. every other field.

| Property          | Type | Required | Description                                                                                                                                                                                                                                              |
|-------------------|---|----------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `uri`             | string | yes      | PK. **The URI** — MERGE key. UUID v4 read from (or, on first ingest, written to) the `.ki/vault.yaml` marker file in the vault root. Independent of user and machine.                                                                                     |
| `name`            | string | yes      | Basename of the vault root directory.                                                                                                                                                                                                                    |
| `displayName`     | string | yes      | Human-friendly display name. Defaults to `name` (directory basename).                                                                                                                                                                                    |
| `path`            | string | yes      | Absolute POSIX path on the ingesting machine. **Machine-scoped** — when multi-user / multi-machine ingest becomes real, `path` should move to the `USES_VAULT` edge so each user can carry their own local path for the same shared vault.               |
| `isObsidianVault` | boolean | yes      | False = plain folder; true = real Obsidian vault.                                                                                                                                                                                                        |
| `description`     | string | no       | User-authored vault purpose / routing hint, read from `.ki/vault.yaml`'s `description:` field on each ingest. Soft-capped at ~8 KB (truncated with a warning if longer). Drives `ki search --type vault` via the `content_search` fulltext index. Removed-from-YAML cleanup is deferred to [#3](https://github.com/zach-blumenfeld/knowledge-index/issues/3). |
| `firstSeenAt`     | datetime | yes      | First-seen. ON CREATE only.                                                                                                                                                                                                                              |
| `lastSeenAt`      | datetime | yes      | Updated on each ingest for vault.                                                                                                                                                                                                                        |

#### `Folder`

Subdirectories inside a vault. Auto-constructed at ingest from the on-disk path of each `:Document` — no separate input, no user-authored metadata, no `description` / `aliases` / `content`. Folders exist so agent-side navigation has a node to land on (`ki tree`, `--under <folder-uri>` scoping); they carry no semantic content of their own. **A `:Folder` is materialised only when at least one indexed `:Document` lives under that path** — empty directories never appear in the graph.

Reversing the v1 "no `:Folder` node" stance: the v1 path-only scheme was queryable via `STARTS WITH` prefix matching but offered nothing for agents wanting to *enumerate* the hierarchy or reason about siblings. With `:Folder` the vault becomes a proper tree — every Folder, Document, and Section has exactly one incoming `:HAS` edge from its parent. Document and Section URIs are unchanged from v1, but their *parent edge* now goes through the folder chain rather than straight to the Vault (see §4.2).

| Property      | Type     | Required | Description                                                                                                                                            |
|---------------|----------|----------|--------------------------------------------------------------------------------------------------------------------------------------------------------|
| `uri`         | string   | yes      | PK. **The URI** — MERGE key formatted as `<vaultId>/<slugified path within vault>` (no trailing `/`). e.g. `<vaultId>/notes`, `<vaultId>/notes/projects`. |
| `name`        | string   | yes      | Basename of the directory (last path segment, slugified).                                                                                              |
| `displayName` | string   | yes      | Human-friendly display name. Defaults to `name`.                                                                                                       |
| `path`        | string   | yes      | Absolute POSIX path on the ingesting machine. **Machine-scoped** — same caveat as `Vault.path` (see §Vault). When multi-machine becomes real, every `*.path` property migrates to a per-user edge or per-user node; tracked indirectly by [#16](https://github.com/zach-blumenfeld/knowledge-index/issues/16). Lets agents jump straight from a `Folder` query to a `Read /path/to/dir` without re-deriving from `Vault.path` + URI prefix. |
| `firstSeenAt` | datetime | yes      | First-seen. ON CREATE only.                                                                                                                            |
| `lastSeenAt`  | datetime | yes      | Updated on each ingest.                                                                                                                                |

No `description`, no `aliases`, no `content`, no `fileHash`. If users want a folder-level note, they put a Document there (e.g. `_index.md`) and that Document carries the metadata, not the Folder.

#### `Document`
Inherits the v2 connector spec (§3) and adds:

**Path conventions.** Document URIs are unchanged from v1: slugified `<vaultId>/<file path within vault>`. The on-disk path's nested directories are *also* materialised as `:Folder` nodes (see above), so the same hierarchy is reachable both via URI prefix match (cheap subtree scan) *and* via `(:Vault|:Folder)-[:HAS*1..]->(:Document)` traversal (cheap enumeration / `ki tree`).

| Source file                               | `Document.uri`                            | Materialised `:Folder` nodes                          |
|-------------------------------------------|-------------------------------------------|-------------------------------------------------------|
| `~/my-vault/ideas.md`                     | `<vaultId>/ideas.md`                      | *(none — document sits at the vault root)*            |
| `~/my-vault/notes/My Projects/Big Idea.md`| `<vaultId>/notes/my-projects/big-idea.md` | `<vaultId>/notes`, `<vaultId>/notes/my-projects`       |
| `~/my-vault/notes/projects/_index.md`     | `<vaultId>/notes/projects/_index.md`      | `<vaultId>/notes`, `<vaultId>/notes/projects`          |

Folder-level metadata (Obsidian folder notes, Hugo `_index.md`, etc.) is still captured by indexing whatever Document lives at that path — the `:Folder` node itself stays intentionally property-poor.

| Property               | Type         | Required | Description                                                                                                                                                                                     |
|------------------------|--------------|----------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `uri`                  | `string`     | Yes      | PK. **The URI** — MERGE key formatted as slugified: `<vaultId>/<file path within vault>` where `<vaultId>` is `Vault.uri`. User is *not* part of the URI — provenance lives on the `LOADED` edge. |
| `name`                 | string       | Yes      | For now use filename (without path, just the basename)                                                                                                                                          |
| `displayName`          | string       | Yes      | For now use filename (without path, just the basename)                                                                                                                                          |
| `path`                 | string       | yes      | Absolute POSIX file path on the ingesting machine. **Machine-scoped** — same caveat as `Vault.path` (see §Vault). Lets agents `Read` the file directly from any `Document` query result; no `Vault.path` join required. |
| `aliases`              | list[string] | no       | Frontmatter aliases (user-authored, ground truth) **plus** piped-wikilink display texts targeting this document (e.g. `[[Darth Vader\|Anakin]]` propagates `"Anakin"` here). Used for wikilink resolution and fulltext recall via the `content_search` index. Wikilink-derived entries are normalized + capped — see [`ingest-cypher.md`](ingest-cypher.md) §4.3 step 7. |
| `fileHash`             | string       | yes      | SHA-256 of file content. Drives incremental sync diffing.                                                                                                                                       |
| `frontmatter`          | string       | no       | JSON-serialised unknown frontmatter keys.                                                                                                                                                       |
| `frontmatterCreatedAt` | datetime     | no       | If frontmatter declares `created:` / `date:`.                                                                                                                                                   |
| `content`              | `string` | No       | Preamble text (any text before the first heading in the file) followed by `uri:` references to direct top-level sections. Shallow content + child pointers — see §4 Content Construction Rules. |
| `sourceType`           | enum         | yes      | `LOCAL_FILE` \| `URL_LINK` \| `WIKILINK_UNRESOLVED`.                                                                                                                                            |
| `firstLoadedAt`        | `datetime` | yes      | Timestamp when the document was ingested. Set on CREATE only (not overwritten on re-ingest).                                                                                                    |
| `lastLoadedAt`         | datetime | yes      | updated on each ingest for vault.                                                                                                                                                               |

#### `Section`
Inherits §3. `Section.uri` is globally unique by virtue of including `Vault.uri` (which is itself globally unique via the marker-file UUID).

| Property        | Type | Required | Description                                                                                                                                                                                                                                                                                                                      |
|-----------------|------|----------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `uri`           | `string`     | Yes      | PK. **The URI** — MERGE key formatted as slugified: `<vaultId>/<file path within vault>#<slugified heading path>`                                                                                                                                                                                                                |
| `name`          | `string` | Yes | for now just the slugified hierarcical heading path                                                                                                                                                                                                                                                                              |
| `displayName`   | `string` | Yes | Heading text of the section (human-readable display name).                                                                                                                                                                                                                                                                       |
| `path`          | `string` | yes | Absolute POSIX path of the **owning Document** on the ingesting machine. Redundant with the parent Document's `path` (every section in a doc shares the same value), but intentional — lets agents `Read` from any `Section` query result without traversing up to the doc. **Machine-scoped** — same caveat as `Vault.path` / `Document.path`. |
| `headingLevel`  | `integer` | Yes | Depth level: `1` = H1, `2` = H2, etc.                                                                                                                                                                                                                                                                                            |
| `content`       | `string` | No | Immediate body text of this section (text between this heading and the first child heading) followed by `uri:` references to direct child sections. Child section text is NOT included. |
| `aliases`       | list[string] | No   | Alternate names that should resolve to this section. Sourced from piped-wikilink display texts targeting this section (e.g. `[[Darth Vader#Origins\|Anakin]]` propagates `"Anakin"` here). Mirrors `Document.aliases`. Defaults to an empty list when written. Covered by the `content_search` fulltext index. See [`ingest-cypher.md`](ingest-cypher.md) §4.3 step 7. |
| `firstLoadedAt`        | `datetime` | yes      | Timestamp when the document was ingested. Set on CREATE only (not overwritten on re-ingest).                                                                                                                                                                                                                                     |
| `lastLoadedAt`         | datetime | yes      | updated on each ingest for vault.                                                                                                                                                                                                                                                                                                |

### 4.2 Relationships

| Type           | From | To | Properties | Parallel Allowed | Description |
|----------------|---|---|---|---|---|
| `USES_VAULT`   | `User` | `Vault` | NO | NO | One per (user, vault) pair. Access edge, **not containment** — kept distinct from `HAS`. |
| `LOADED`       | `User` | `Document` | YES - See Below Property Table | YES | One per (user, document). MERGE-upsert on each ingest: `ON CREATE SET firstLoadedAt=...`, `ON MATCH SET lastLoadedAt=...,`. Captures **provenance** — who/what/when loaded each document, and from which machine. |
| `LOADED`       | `User` | `Vault` | YES - See Below Property Table | YES | One per (user, vault). Tracks vault-level ingest provenance. |
| `HAS`          | `Vault\|Folder\|Document\|Section` | `Folder\|Document\|Section` | NO | NO | **The** containment edge. Each child node has exactly one incoming `HAS`. See *Valid `HAS` endpoint pairs* below. Walks of the form `(root)-[:HAS*1..N]->(descendant)` work across the whole hierarchy uniformly — caller filters by descendant label as needed. |
| `NEXT_SECTION` | `Section` | `Section` | NO | NO | Linear chain threading **all** sections of a document in DFS reading order (top to bottom as a human would read the file). Crosses heading levels — an H1's last descendant's `NEXT_SECTION` points to the next H1, not to a sibling at the same level. Lets retrieval do cheap `±N` windowing and full-text-order walks without parsing `uri:` pointer lines out of `content`. Re-built per ingest (delete then re-create — see §4.3). Sequence, **not containment** — kept distinct from `HAS`. |
| `LINKS_TO`     | `Document\|Section` | `Document\|Section` | YES - See Below Property Table | NO | Includes wikilinks; `wikilink=true` marks Obsidian `[[...]]` origin. Cross-tree reference, **not containment** — kept distinct from `HAS`. |

**Valid `HAS` endpoint pairs.** Enforced by ingest (not by Neo4j's relationship-type system, which doesn't constrain endpoint labels). Anything else is a bug.

| Parent label | Child label | Notes |
|--------------|-------------|-------|
| `Vault`    | `Folder`   | Top-level folder (immediate child of the vault root). |
| `Vault`    | `Document` | Root-level document (no enclosing folder). |
| `Folder`   | `Folder`   | Nested subdirectory. |
| `Folder`   | `Document` | Document nested under that folder. |
| `Document` | `Section`  | Top-level section (H1, or a higher heading that's the document's first heading). |
| `Section`  | `Section`  | Nested heading. |

Each `Folder` / `Document` / `Section` has **exactly one** incoming `HAS` edge. The Vault itself has zero — it's the root.

**Why one relationship type instead of three (`HAS_FOLDER` / `HAS_DOCUMENT` / `HAS_SECTION`).** All three would be different *names* for the same semantic ("parent in the containment tree"). Neo4j can naturally express "any of these types" via `[:A|B|C]` alternation, but for a hierarchy where every containment edge has the same meaning, separate names add ceremony without information — the endpoint labels already carry "what kind of containment." Single-type `HAS` lets us write tree walks as `[:HAS*]` instead of `[:HAS_FOLDER|HAS_DOCUMENT|HAS_SECTION*]`, and makes the single-parent invariant trivial to state and lint. Non-containment edges (`USES_VAULT`, `LOADED`, `NEXT_SECTION`, `LINKS_TO`) keep their own types because they mean different things.

> ** __Parallel Allowed__ indicates whether multiple instances of the same relationship type can exist between the same pair of nodes; for example, a User can have multiple LOADED relationships to a Document (one per ingest), whereas a User has only one USES_VAULT relationship per Vault. In the Case of parellel relationships a MERGE key is required to uniquely identify relationships (since multiple may be to/from the same nodes)

> **Why `LOADED` carries the agent/model props (and not the `User` node itself):** Useful for provenance ("which agent loaded *this* doc"). This belongs on the relationship, because a single user can ingest with different agents over time (one-off CLI run vs. agent-driven sync). Keep both. Don't try to deduplicate.

#### LOADED Properties
| Property | Type | Required | Description                                                                                           |
|---|---|---|-------------------------------------------------------------------------------------------------------|
| `loadId` | string | yes | Stable UUID. MERGE key for `userId` to  `document uri` and `vault uri`.  .                            |
| `loadedAt` | datetime | yes | Timestamp of when document or vault was loaded. Updated on each ingest.                               |
| `agentName` | string | no | Agent that performed the load (e.g. `claude-desktop`, `claude-code`).                                 |
| `agentVersion` | string | no | Version of the agent that performed the load.                                                         |
| `modelId` | string | no | LLM model used during load (e.g. `claude-opus-4-7`).                                                  |
| `graphVaultVersion` | string | no | `graph_vault.__version__`. Which Graph Vault did the ingest.                                          |
| `timezone` | string | no | `datetime.now().astimezone().tzname()` / `zoneinfo`. IANA tz; for interpreting `loadedAt` timestamps. |
| `locale` | string | no | `locale.getdefaultlocale()`. E.g. `en_US.UTF-8`.                                                      |
| `os` | string | no | `platform.system()`. `Darwin` / `Linux` / `Windows`.                                                  |
| `osVersion` | string | no | `platform.release()`.                                                                                 |
| `hostname` | string | no | `socket.gethostname()`. Identifies the machine that ran the import.                                   |
| `pythonVersion` | string | no | `platform.python_version()`. Repro / debugging provenance.                                            |

#### LINKS_TO Properties

| Property | Type | Required | Description                                                         |
|---|---|---|---------------------------------------------------------------------|
| `embed` | boolean | yes | Whether the link is an embedded reference (`![[...]]` in markdown). |
| `wikilink` | boolean | yes | Whether the link is a wikilink (`[[...]]` in markdown).             |


### Content Construction Rules

These rules should be implied in a document mapper not in individual parsers. Parsers produce raw parsed document trees; the mapper applies these rules when building entity `content` fields.

**Rule 1 — Shallow content with child pointers:**
Each node's `content` field contains only the body text directly under its own heading, followed by `uri:` references to its direct children. Child body text is never included.

```
## Installation
blal blal bla

uri:/docs/guide.md#installation/python
uri:/docs/guide.md#installation/cli
```

The `uri:` prefix is a deliberate sentinel — it is unambiguous, easy to parse programmatically, and signals to the agent that deeper content exists and can be retrieved by traversing `HAS` relationships.

**Rule 2 — Skipped heading levels:**
If a document jumps from H1 to H3 (skipping H2), the H3 becomes a **direct child** of the H1 in the tree. The `HAS` edge goes from the H1 Section (or Document) directly to the H3 Section. `headingLevel` on the node accurately reflects `3`. The parent's content lists the H3 URI as a direct child pointer. No synthetic H2 node is created.

**Rule 3 — Duplicate heading disambiguation:**
Duplicate headings at the same nesting level are disambiguated by appending `-1`, `-2`, etc. starting from the **second** occurrence (GitHub/Pandoc convention):
- First `## Installation` → `#installation`
- Second `## Installation` → `#installation-1`
- Third `## Installation` → `#installation-2`

Disambiguation is scoped per parent: two sections named "Overview" under different H2 parents do not conflict with each other.