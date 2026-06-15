# Profiles, vaults, config, and scoping

The mental model under every `ki` command. Read this first; the search, index,
and status docs assume it.

---

## 0. Mental model

`ki` is a command-line tool for searching and analyzing knowledge bases (KBs). For
`ki`, a KB is a directory of markdown documents on a filesystem — capabilities may
expand in the future, but that's what it is for now. We call that directory a
**vault**.

Most `ki` usage targets one local vault at a time, but it generalizes to remote
vaults and cross-vault searches.

To maintain scope and privacy boundaries, `ki` groups vaults under a **profile**,
which carries its own access credentials. Each vault belongs to exactly one profile,
and a profile can hold multiple vaults. Profiles are backed by
[Neo4j](https://neo4j.com) instances (detailed below).

## 1. The three nouns

- **Profile** — a named **Neo4j connection** (uri + credentials, in `config.yaml`).
  It's the database, and it's a **privacy/isolation boundary**: "personal" vs
  "work" are different profiles pointing at different Neo4j instances/databases.
- **Vault** — a **directory of markdown** that `ki` has marked and indexed; one
  vault maps to one knowledge base. It's the unit of indexing and search, and is
  identified by a slug **uri**.
- **The binding** — each vault is **bound to exactly one profile** (recorded in
  the vault's `.ki/vault.yaml`). That binding is how a vault knows which database
  it lives in, with no global registry and no prompting.

One profile can hold **multiple** vaults. A vault belongs to **one** profile.

```
profile "personal"  ──┬── vault  my-notes
 (bolt://…:7687)       └── vault  journal

profile "work"  ──────┬── vault  api-docs
 (neo4j+s://…)         └── vault  runbooks
```

**Addressing.** Inside a vault, every folder, document, and section has a
hierarchical **uri** — a path like `my-notes/projects/plan.md#goals` that encodes
where it sits. Copy uris from `ki outline` / `ki search`; trim a trailing segment
to get the ancestor (no query). It's the identifier you feed to `ki get <uri>`,
`ki outline <uri>`, and `ki search --under <uri>`. Full scheme:
[`data-model/schema.md`](data-model/schema.md) *The URI scheme*.

---

## 2. Where state lives

Three stores, each authoritative for a different thing:

| Store | Location | Holds | Authoritative for |
|---|---|---|---|
| **`config.yaml`** | `~/.config/ki/config.yaml` (XDG-first; fallback `~/.ki/config.yaml`), mode `0600` | profiles (connections + creds), `default_profile` | *which databases exist* |
| **`.ki/vault.yaml`** | inside each vault dir | the vault's `uri`, bound `profile` name, `description` | *this directory's identity + binding* |
| **Neo4j** | the profile's instance | the graph (documents, sections, folders, links) + the `:Vault` nodes | *which vaults exist + their content* |

Key consequences:
- **`config.yaml` does not track vaults.** The list of vaults lives in Neo4j.
- **`.ki/vault.yaml` stores only the profile *name*** — never credentials — so it's
  safe to commit alongside the source. Clone the repo on another machine and the
  vault still knows its profile (you just need that profile in your `config.yaml`).

### `config.yaml` shape

```yaml
default_profile: personal
profiles:
  personal:
    uri: "bolt://localhost:7687"
    user: "neo4j"
    password: "..."
    source: "local-podman"     # local-podman | aura | existing
    database: "neo4j"          # optional; omit → server's home database
  work:
    uri: "neo4j+s://xxxx.databases.neo4j.io"
    user: "neo4j"
    password: "..."
    source: "aura"
```

- `source` records how the profile was set up (drives troubleshooting hints).
- `database` is optional. **Omit it** to use the server's home database — correct
  for standard Neo4j *and* Aura (whose home db is the instance DBID). Never hard-set
  it to `"neo4j"` blindly; that breaks Aura Free.

### `.ki/vault.yaml` shape

```yaml
uri: my-notes            # ki-owned slug, write-once per vault
profile: personal        # which profile (Neo4j) this vault lives in
description: "Research notes on X."   # user-authored routing hint
```

The **uri** is a slug derived from the directory basename on first index
(`~/my-notes` → `my-notes`). If that slug is already taken on the same Neo4j, a
`-N` suffix is appended (`my-notes-2`). Once assigned it's pinned in this file and
reused on every re-index. **The basename is not always the uri** (because of `-N`)
— so copy uris from `ki outline`/`ki search`, don't hand-type them.

---

## 3. Working with `ki`: the command surface and how it scopes

### 3.1 The command surface

`ki` commands fall into four groups. **`ki` only reads and indexes your markdown —
it never edits your source files.** "Remove" commands remove *index* entries; the
files on disk are untouched. (See `docs/general-philosophy.md`.)

| Group | Commands | What they do |
|---|---|---|
| **Index & update (write)** | `index`, `add`\*, `rm`\*, `mv`\* | Build/refresh the graph from source markdown. `index` = full (re)build of a vault; `add`/`rm`/`mv` = incremental per-target. (\* planned) |
| **Search & retrieval (read)** | `search`, `outline`, `get`, `status` | Query and read the index. No source files needed. |
| **Remove from index** | `drop`, `nuke` | `drop` = one vault; `nuke` = a whole profile. Index only — source files stay. |
| **Admin & info** | `configure`, `profile list`, `vault list`, `init`, `skill` | Manage profiles, inspect what exists, install the agent skill. |

### 3.2 How `ki` scopes profiles and vaults

Resolving the right **profile** (which Neo4j) and **vault(s)** (which subtree) is
what keeps `ki` correct and private. The surface is optimized for the common case —
**one local vault at a time** — and generalizes from there. The single lever that
takes you out of the default is **`--profile`**: passing it means *"I'm not working
on the vault I'm standing in."*

> **Agent-skill coverage.** The bundled `knowledge-base` agent skill (`SKILL.md`)
> routes **mode 1 only** (local, single vault — the 95% case). Modes 2 and 3 are
> **CLI-only** today; agent-skill coverage for them is deferred to a future
> `knowledge-base-reader` skill ([#66](https://github.com/zach-blumenfeld/knowledge-index/issues/66)).
> See `docs/skills.md`.

#### 3.2.1 The usual path — a local vault, one at a time

You have the markdown on your filesystem and work one KB at a time.

- **Profile and vault both come from where you are** — the `.ki/vault.yaml` at or
  above your working dir (cwd, or `-C <dir>`). No `--profile`/`--vault`.
- **First index binds the profile**: `ki index . --profile <p>` — chosen once (a
  privacy boundary; never defaulted) and written into the marker. Every later
  command reads that binding.
- **The full lifecycle is available** — index/update, search/retrieve, drop.

```sh
cd ~/my-notes
ki status                                   # NOT_A_VAULT? → first index:
ki index . --profile personal --description "..."
ki outline my-notes --full                  # get oriented
ki search "..."                             # find the right slice
ki get --type full "<uri>"                  # retrieve full content
# ...edit files...
ki index .                                  # resync (or `ki add <target>` once built)
```

#### 3.2.2 Remote vaults — the index without the source

A vault is indexed in Neo4j but you **don't have its source files locally** (someone
else indexed it; or it's a shared/production instance). `ki` supports **full search
and retrieval** here — useful for giving many users (often read-only) access to a KB,
and for powering production systems and apps.

- **Name the target explicitly** — there's no local `.ki` to resolve from: pass
  **`--profile`** (always) and identify the vault with **`--vault <uri>`** (search)
  or the **uri argument** (`outline`/`get`).
- **Reads work; source-dependent writes don't.** `search`/`outline`/`get` are fine.
  `index`/`add`/`mv` need the source files, so **you can't update data in this
  mode**. `drop`/`nuke` (index removal) *are* allowed — they don't need source.
- **Read-only is enforced by Neo4j, not `ki`.** For true read-only access, use the
  instance's [role-based access controls](https://neo4j.com/docs/operations-manual/current/authentication-authorization/manage-privileges/).

> "Remote" means *you lack the source files* — independent of where Neo4j runs.
> Local Podman and remote Aura are both reachable in any pattern here.

#### 3.2.3 Cross-vault search — many vaults in one profile

Not a primary use case, but searching across **multiple vaults in the same profile**
is supported. Only meaningful for the **navigation/read** commands — `search`,
`outline`, `get`:

- **`--profile P`** alone → all vaults in P (global).
- **`--profile P --vault a,b`** → just those vaults.

Two caveats:
1. **Indexing is per-vault**, so there are **no internal links across vaults**. The
   only cross-vault connection is a shared *external* target (e.g. two vaults linking
   the same URL → the same stub).
2. **Cross-profile search is not supported** — only cross-vault *within* one profile
   (one Neo4j database).

#### 3.2.4 Special cases

- **`nuke`** ignores vault scope — it wipes a whole **profile** (all its vaults).
  Last resort; typed confirmation.
- **`configure` / `profile list`** are **config-only** — no vault, no Neo4j
  connection (they work before any vault exists).
- **`init`** writes a `.ki/vault.yaml` marker without indexing (advanced).

---

## 4. How a command resolves its profile

§4 and §5 are the *mechanism* behind §3 — how `ki` actually computes the profile and
vault from your inputs.

Most commands resolve the profile with one shared rule (`resolve_profile`), highest
precedence first:

1. **`--profile <name>`** — explicit override, wins over everything.
2. **The vault's binding** — walk up from the working dir (cwd, or `-C <dir>`) to
   `.ki/vault.yaml`, and use its `profile`. **The normal path: each vault owns its
   profile.**
3. **`$KI_PROFILE`** if set.

`profile` is never assumed by default.

If the vault names a profile that isn't in `config.yaml` (renamed, or cloned to a
new machine), that's a clear error → add the profile with `ki configure`, or
re-bind to an existing one by re-indexing: `ki index . --profile <p>`.

> **`ki search` is stricter** — see §3.2. It never silently falls back to a default,
> because *which database you searched* should never be a guess.

## 5. How a command resolves its vault

Same walk-up everywhere — there is **no stored "current vault."** It's computed
from *where you run the command*:

- start at the working dir (the shell's cwd, or **`-C <dir>`** if passed),
- walk **up** to the nearest `.ki/vault.yaml` (`find_vault_root`),
- first hit wins; none found → not in a vault.

`-C <dir>` just means "resolve as if I were working in `<dir>`" — the single lever
for pointing `ki` at a vault you're not cd'd into. (Remote / cross-vault work names
the target explicitly instead; see §3.2.)

---

## 6. `ki status` — is this directory ready?

`ki status [path]` answers "can I use `ki` here, and if not, what's the one next
step?" It resolves in **layers**, each needing the one above to pass, and reports
the **first blocking state**:

1. **Disk** (no Neo4j needed) — is there a `.ki/` marker here?
2. **Config** — is the bound profile actually in `config.yaml`?
3. **Neo4j reachability** — it *attempts the connection* and classifies the result.
4. **Graph state** — only knowable once Neo4j answers.

| State | How it's known | Next step |
|---|---|---|
| `NOT_A_VAULT` | no `.ki/` on disk | pick a profile, `ki index . --profile <p>` |
| `PROFILE_MISSING` | marker names a profile not in config | `ki configure` (add it) or `ki index . --profile <p>` (re-bind) |
| `NEO4J_DOWN` | connect → nothing listening | start Neo4j |
| `NEO4J_UNRESPONSIVE` | connect hangs / times out | wait, then troubleshoot |
| `AUTH_ERROR` | connect → auth failure | re-enter creds (`ki configure`) — *not* a restart |
| `NOT_INDEXED` | reachable, but no `:Vault` node | `ki index .` |
| `STALE` | indexed, but `.md` set / hashes drifted | `ki index .` to refresh |
| `READY` | indexed + in sync | use it (`outline` → `search`/`get`) |

Exit code is `0` only for `READY` (so `ki status && ki search …` composes). The
disk + config layers work even when Neo4j is down — that's how status can report
the Neo4j rows at all.

> `STALE`/`READY` is **markdown-only**: it tracks `.md` files, not linked
> attachments (PDFs, images captured as stub nodes). After bulk/attachment changes,
> a full `ki index .` is the source of truth.

---

## See also

- `docs/commands/search.md` — the full `ki search` model (Lucene, `--under`, the scope
  predicate, stubs).
- `docs/data-model/schema.md` — node labels, uri conventions, containment.
