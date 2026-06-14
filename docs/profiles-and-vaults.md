# Profiles, vaults, config, and scoping

The mental model under every `ki` command. Read this first; the search, index,
and status docs assume it.

---

## 0. Mental Model
`ki` is a command line tool for searching and analyzing knowledge bases (KB)s.
for `ki`, a KB is defined as a directory of markdown documents on a file system (capabilities may expand in the future, but this is what it is for now)

We call this directory of markdown docs a **vault**.  

Most `ki` usage patterns target working with one local vault at a time. Though they generalize to working with remote vaults and cross vault searches. 

To maintain scope and privacy boundaries, `ki` has the concept of a **profile** which has its own access credentials. Each vault belongs to exactly one profile, and a profile can contain multiple vaults. Profiles are backed by [Neo4j](https://neo4j.com) instances explained in more detail below. 

## 1. The three nouns

- **Profile** — a named **Neo4j connection** (uri + credentials, in `config.yaml`).
  It's the database, and it's a **privacy/isolation boundary**: "personal" vs
  "work" are different profiles pointing at different Neo4j instances/databases.
- **Vault** — a **directory of markdown** that `ki` has marked and indexed. Conceptually a single vault maps to a single knowledge base (KB). A vault is the
  unit of indexing and search. Identified by a slug **uri**.
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

## 3. How a command finds its profile

Most commands resolve the profile with one shared rule (`resolve_profile`),
highest precedence first:

1. **`--profile <name>`** — explicit override, wins over everything.
2. **The vault's binding** — walk up from the working dir (the shell's cwd, or **`-C <dir>`** if passed) to `.ki/vault.yaml`, use
   its `profile`. **This is the normal path: each vault owns its profile.**
3. `$KI_PROFILE` if set.

`-C <dir>` just means "resolve as if I were working in `<dir>`." That's the single
lever for pointing `ki` at a vault you're not cd'd into.

`profile` is never assumed by default. 

If the vault names a profile that isn't in `config.yaml` (renamed, or cloned to a
new machine), that's a clear error → add the profile with `ki configure`, or
re-bind to an existing one by re-indexing: `ki index . --profile <p>`.

> **`ki search` is stricter** — see §5. It never silently falls back to a default,
> because *which database you searched* should never be a guess.

## 4. How a command finds its vault

Same walk-up everywhere — there is **no stored "current vault."** It's computed
from *where you run the command*:

- start at the working dir (the shell's cwd, or **`-C <dir>`** if passed),
- walk **up** to the nearest `.ki/vault.yaml` (`find_vault_root`),
- first hit wins; none found → not in a vault.



---

## 5. Scoping by operation class

### Index ops — `ki index`, `ki drop`, `ki nuke`

Operate on **the directory you point at** (positional path or cwd).

- **`ki index <dir>`** — first index **binds** the profile: you must choose it
  (`--profile`), and it's written into the new `.ki/vault.yaml`. Never defaulted —
  the profile is a privacy boundary. Re-index **honors the marker's** uri + binding
  (so a synced vault keeps its identity across machines). Indexing fully rebuilds
  that vault's graph.
- **`ki drop <vault>`** — removes one whole vault from the index (source files
  untouched). Profile resolved as in §3.
- **`ki nuke`** — wipes an entire profile's graph (all vaults). Last resort.

### Read ops — `ki status`, `ki outline`, `ki get`, `ki search`

- **Profile** via §3 (`ki search` stricter, below).
- **Vault** via §4 (cwd / `-C`).
- **`ki outline <uri>` / `ki get <uri>`** take a uri to address *what* to read; the
  profile/vault still resolve from where you are (or `-C`).
- **`ki search`** has its own scope model (the reason this doc exists):

  - **Local mode (default)** — profile *and* vault both come from the dir you're in
    (cwd or `-C`). Narrow within it using **`--under <path-or-uri>`**. You never
    type `--profile`/`--vault`. This is the 95% path.
  - **Remote mode (explicit)** — opt in with `--profile`:
    - `--profile P` → **global** search across all vaults in P.
    - `--profile P --vault <uri,…>` → just those vaults.

  So `--profile` is the *mode switch into remote*, not a silent scope-changer.
  `--under` is the local filter; `--vault` is the remote selector. (See
  `docs/search.md` for the full search picture.)

### Write ops — incremental edits

Keep the index in step with the filesystem **per target**, scoped to the vault you're
in, without a full rebuild:

- `ki add <doc|folder>` — upsert one document or folder.
- `ki rm <doc|folder>` — remove one document or folder (not a whole vault — that's
  `ki drop`).
- `ki mv <old> <new>` — rename/move, updating the graph in place.

Profile + vault resolve exactly as for read ops (§3/§4); the target is addressed by
path or uri within that vault. *(Status: `index`/`drop`/`nuke` are built; the
incremental `add`/`rm`/`mv` are the planned write surface — until then, re-`index`
the vault.)*

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

- `docs/search.md` — the full `ki search` model (Lucene, `--under`, the scope
  predicate, stubs).
- `docs/data-model.md` — node labels, uri conventions, containment.

<!--

## `ki` commands
...quick overview of them broken out by type
### Indexing and Updates (Write)

### Search & Retrieval (Read)

### Droping & Removing

** reminder here that `ki` does not edit source data it is just an index.  see docs/general-phylosophy (spelled worng)

### Managment/Admin /Info (not sure on right name..but basically all those)

## How ki commands scope profiles and vaults

Scoping to the right profile and vault(s) is critical for all `ki` commands to maintain correctness and security.
We expect that most users will want to work with personal local KBs so the command surface prioritizes that use case, but as stated earlier this generalizes

### The Usual Path: Vault on Local File System w/ One Vault at a time

...specified in working dir or with -C
for index on first binding profile is required, etc. 
....explain the working process here. 

### Working with Remote Vaults

...specifically vaults not located on the local file system (Neo4j instace can be remote or not in any of these modes)
....soecify --profile and --vault
Note that some `ki` indexing commands (`index`) need source files so this mode doesn't allow updating data
....

### Cross-Vault Search & Retrieval

While not a primary use case at the moment, searching across multple vaults within a profile is possible.

This is only relevand for search and navigation commands: search, outline, and get 

passing --profile by itself to search & outline overrides both profile and any vault setting, it implies retrieval over all vaults

Important Caveats: 
1. Indexing is done per vault so cross-vault linking is only possible for some external links,  i.e. url-links. 
2. cross-profile search is not supported. Only cross-vaults within the same profile

### Spoecial Considerations: Nuke, configure,...etc. 



### Working with one local vault

### Working with a Remote Vault

### Cross-Vault Searches


-->