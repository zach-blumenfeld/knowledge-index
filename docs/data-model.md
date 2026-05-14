
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

Vault identity is carried by a `.ki/vault-id` marker file written into the vault root on first ingest (same trick as `.git/`, `.obsidian/`, JetBrains `.idea/`). The marker travels with the folder, so a vault synced across machines via Dropbox / iCloud / git resolves to the **same** `:Vault` node — independent of user, independent of machine. Multiple users can therefore `USES_VAULT` the same vault.

| Property          | Type | Required | Description                                                                                                                                                                                                                                              |
|-------------------|---|----------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `uri`             | string | yes      | PK. **The URI** — MERGE key. UUID v4 read from (or, on first ingest, written to) the `.ki/vault-id` marker file in the vault root. Independent of user and machine.                                                                                       |
| `name`            | string | yes      | Basename of the vault root directory.                                                                                                                                                                                                                    |
| `displayName`     | string | yes      | Human-friendly display name. Defaults to `name` (directory basename).                                                                                                                                                                                    |
| `path`            | string | yes      | Absolute POSIX path on the ingesting machine. **Machine-scoped** — when multi-user / multi-machine ingest becomes real, `path` should move to the `USES_VAULT` edge so each user can carry their own local path for the same shared vault.               |
| `isObsidianVault` | boolean | yes      | False = plain folder; true = real Obsidian vault.                                                                                                                                                                                                        |
| `firstSeenAt`     | datetime | yes      | First-seen. ON CREATE only.                                                                                                                                                                                                                              |
| `lastSeenAt`      | datetime | yes      | Updated on each ingest for vault.                                                                                                                                                                                                                        |

#### `Document`
Inherits the v2 connector spec (§3) and adds:

**Path conventions.** Nested directories inside the vault are encoded in the URI path — there is **no `:Folder` node** in v1. Slugify each path *segment* independently and keep `/` as the segment separator, so the hierarchy stays queryable via prefix match (e.g., `WHERE d.uri STARTS WITH $vaultId + '/notes/projects/'` returns all docs in that subtree).

| Source file                               | `Document.uri`                            |
|-------------------------------------------|-------------------------------------------|
| `~/my-vault/ideas.md`                     | `<vaultId>/ideas.md`                      |
| `~/my-vault/notes/My Projects/Big Idea.md`| `<vaultId>/notes/my-projects/big-idea.md` |
| `~/my-vault/notes/projects/_index.md`     | `<vaultId>/notes/projects/_index.md`      |

Folder-level metadata (Obsidian folder notes, Hugo `_index.md`, etc.) is captured by indexing whatever Document lives at that path — no special handling. Empty folders simply don't appear in the graph.

| Property               | Type         | Required | Description                                                                                                                                                                                     |
|------------------------|--------------|----------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `uri`                  | `string`     | Yes      | PK. **The URI** — MERGE key formatted as slugified: `<vaultId>/<file path within vault>` where `<vaultId>` is `Vault.uri`. User is *not* part of the URI — provenance lives on the `LOADED` edge. |
| `name`                 | string       | Yes      | For now use filename (without path, just the basename)                                                                                                                                          |
| `displayName`          | string       | Yes      | For now use filename (without path, just the basename)                                                                                                                                          |
| `aliases`              | list[string] | no       | From frontmatter — used for wikilink resolution if present                                                                                                                                      |
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
| `headingLevel`  | `integer` | Yes | Depth level: `1` = H1, `2` = H2, etc.                                                                                                                                                                                                                                                                                            |
| `content`       | `string` | No | Immediate body text of this section (text between this heading and the first child heading) followed by `uri:` references to direct child sections. Child section text is NOT included. |
| `firstLoadedAt`        | `datetime` | yes      | Timestamp when the document was ingested. Set on CREATE only (not overwritten on re-ingest).                                                                                                                                                                                                                                     |
| `lastLoadedAt`         | datetime | yes      | updated on each ingest for vault.                                                                                                                                                                                                                                                                                                |

### 4.2 Relationships

| Type           | From | To | Properties | Parallel Allowed | Description |
|----------------|---|---|---|---|---|
| `USES_VAULT`   | `User` | `Vault` | NO | NO | One per (user, vault) pair. |
| `LOADED`       | `User` | `Document` | YES - See Below Property Table | YES | One per (user, document). MERGE-upsert on each ingest: `ON CREATE SET firstLoadedAt=...`, `ON MATCH SET lastLoadedAt=...,`. Captures **provenance** — who/what/when loaded each document, and from which machine. |
| `LOADED`       | `User` | `Vault` | YES - See Below Property Table | YES | One per (user, vault). Tracks vault-level ingest provenance. |
| `HAS_DOCUMENT` | `Vault` | `Document` | NO | NO | One per (vault, document). Replaces the v1 draft's `IN_VAULT`. A document belongs to exactly one vault. |
| `HAS_SECTION`  | `Document\|Section` | `Section` | NO | NO | Tree edge. Same as v2 connector spec. |
| `NEXT_SECTION` | `Section` | `Section` | NO | NO | Linear chain threading **all** sections of a document in DFS reading order (top to bottom as a human would read the file). Crosses heading levels — an H1's last descendant's `NEXT_SECTION` points to the next H1, not to a sibling at the same level. Lets retrieval do cheap `±N` windowing and full-text-order walks without parsing `uri:` pointer lines out of `content`. Re-built per ingest (delete then re-create — see §4.3). |
| `LINKS_TO`     | `Document\|Section` | `Document\|Section` | YES - See Below Property Table | NO | Includes wikilinks; `wikilink=true` marks Obsidian `[[...]]` origin. |

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

The `uri:` prefix is a deliberate sentinel — it is unambiguous, easy to parse programmatically, and signals to the agent that deeper content exists and can be retrieved by traversing `HAS_SECTION` relationships.

**Rule 2 — Skipped heading levels:**
If a document jumps from H1 to H3 (skipping H2), the H3 becomes a **direct child** of the H1 in the tree. `HAS_SECTION` goes from the H1 Section (or Document) directly to the H3 Section. `headingLevel` on the node accurately reflects `3`. The parent's content lists the H3 URI as a direct child pointer. No synthetic H2 node is created.

**Rule 3 — Duplicate heading disambiguation:**
Duplicate headings at the same nesting level are disambiguated by appending `-1`, `-2`, etc. starting from the **second** occurrence (GitHub/Pandoc convention):
- First `## Installation` → `#installation`
- Second `## Installation` → `#installation-1`
- Third `## Installation` → `#installation-2`

Disambiguation is scoped per parent: two sections named "Overview" under different H2 parents do not conflict with each other.