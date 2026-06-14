# How `ki search` works

`ki search` is full-text retrieval over a vault's knowledge graph. It runs **one
ranked sweep** over the documents and sections in scope and returns the matching
*slices* — uri, title, score, and content — so an agent pulls just what it needs
instead of grepping and reading whole files.

This doc is the holistic picture: the index, the query, scope resolution
(local vs remote, `--under` and `--vault`), result shaping, and the external-stub
nuance. For the underlying profile/vault model it builds on, see `docs/scoping.md`.

---

## 1. The index

Indexing builds a single Neo4j full-text index, `content_search`, over three
node labels:

```
(:Document | :Section | :Vault)  ON EACH  [displayName, content, aliases, description]
```

- **Standard analyzer** — lexical, lowercased, whitespace/punctuation tokenized.
  It does *not* understand synonyms or meaning (see *Semantic expansion*), and it
  shreds punctuation-heavy strings like URIs (which is why scoping is a Cypher
  predicate, never a Lucene clause — see §5.4).
- **Fields** — a hit can come from a node's title (`displayName`), its body
  (`content`), its wikilink `aliases`, or a vault/section `description`.

`ki search` only ever returns **Documents and Sections**. Vaults are in the index
(so a vault `description` can match) but are filtered out of results by label.

---

## 2. One unified sweep (Documents + Sections)

There is a single search path — one query over **both** Documents and Sections at
once (`SEARCH_DOC_SECTION`). There is no separate "title search" vs "section
search" mode.

Why both by default: in ki's data model a **Document's `content` is just its
header/intro** (everything above the first heading); the rest of the body lives
in its **Sections**. So searching documents alone misses most of the prose, and
searching sections alone misses the intro and single-section notes. Sweeping both
is the right default.

Narrow with `--types` when you have reason to:

```sh
ki search "token refresh"                      # documents + sections (default)
ki search "token refresh" --types section      # sections only
ki search "token refresh" --types document     # documents only (intros/titles)
```

`--types` is a comma-separated subset of `{document, section}`; omitting it means
both.

---

## 3. Query syntax (Lucene)

Queries are **Lucene query syntax**, passed through to the full-text index:

- `AND` / `OR` / `NOT` (or `+` / `-`)
- `"exact phrases"`
- `()` grouping
- `*` wildcards, `~` fuzzy

```sh
ki search '+auth +"token refresh" -deprecated'
ki search 'postgres AND (migration OR rollback)'
```

Reference: <https://lucene.apache.org/core/8_2_0/queryparser/org/apache/lucene/queryparser/classic/package-summary.html>

---

## 4. Semantic expansion (the lexical gap)

The index matches **tokens, not meaning**. A search for `"Darth Vader"` will not
find a note that only ever says `"Anakin Skywalker"`. Account for this by
rewriting the term into a few alternates you know and running them — either as
extra calls or one OR-form query:

```sh
ki search 'Anakin OR "Darth Vader" OR Vader'
```

Do this **reactively** when results look thin (few hits, low scores, no hits), or
**pre-emptively** when you already expect the vault's wording to differ from the
query's.

---

## 5. Scope resolution

Every search resolves to **one profile** (which Neo4j) and an **optional scope**
(which vault[s] / subtree). It's computed fresh per invocation — there is **no
stored "current vault."** Two modes, and the lever between them is `--profile`.
(This is the search-specific view of the model in `docs/scoping.md` §3.2.)

### 5.1 Local mode (default)

You're working in a vault on disk. **Profile and vault both come from where you
run** — walk up from the working dir (the shell's cwd, or `-C <dir>`) to the
nearest `.ki/vault.yaml` (`find_vault_root`). That marker yields the vault's
**uri**, its **root path**, and its **bound profile** — the three inputs to
scoping. No `--profile`/`--vault` needed; narrow within the vault with `--under`.

The resolved scope is printed to stderr, so it's never a silent guess:

```
ki: profile 'personal' · vault 'my-notes'   (from .ki)
```

Profile is **never guessed**: it's `--profile`, else the vault's binding. Not in a
vault and no `--profile` → error (nothing to resolve against).

### 5.2 `--under` — the local subtree filter

`--under` narrows to a containment subtree at **any level** — folder, document, or
section of the vault you're in. It takes **either a uri or a filesystem path**,
resolved by one function (a uri in this vault → as-is; an existing file/dir in this
vault → its uri; neither → loud error):

```python
def resolve_to_uri(arg, vault_uri, vault_path, cwd) -> str:
    # 1. already a uri in this vault?
    if arg == vault_uri or arg.startswith(vault_uri + "/"):
        return arg.rstrip("/")
    # 2. an existing file/dir in the vault?
    p = Path(arg).expanduser()
    if not p.is_absolute():
        p = cwd / p
    p = p.resolve()
    if p.exists() and p.is_relative_to(vault_path):
        rel = p.relative_to(vault_path).as_posix()
        return vault_uri if rel == "." else f"{vault_uri}/{slugify_path(rel)}"
    # 3. neither → loud error, never a silent miss
    raise click.ClickException(f"{arg!r} is not a uri or a file in vault {vault_uri!r}")
```

`vault_uri`/`vault_path` are always present here because `--under` is **local-only**
(you're in a vault). A path is **`-N`-safe**: it resolves through the on-disk marker,
so even if the directory is `my-notes/` but its slug collided to `my-notes-1`, the
path still yields `my-notes-1/...`. A *guessed bare uri* is not safe — the basename
is not always the uri. Rule of thumb: **copy uris** from `ki search`/`ki outline`,
or **use a path** when you mean "the folder I'm looking at."

### 5.3 Remote mode (`--profile`)

Passing `--profile` says *"I'm not working on the vault I'm standing in."* It drops
the cwd-vault auto-scope (a vault lives in exactly one profile) and searches the
profile directly — useful when you don't have the source locally (shared / remote
KBs). Scope is by **`--vault`**, not `--under`:

- `--profile P` → **all vaults** in P.
- `--profile P --vault a,b` → just those vaults (comma-separated **uris**, taken
  verbatim — no filesystem resolution, since the source may not be present).

```
ki: profile 'work' · all vaults
ki: profile 'work' · vaults [api-docs, runbooks]
```

### 5.4 The scope predicate (one or many uris)

The scope is **not** a plain prefix. Containment in ki uris uses two separators —
`/` (vault/folder → child, section → subsection) and `#` (document → its sections)
— and a node's own uri has no trailing separator. So "node `X` and everything under
it" is a **three-part, type-agnostic** test, wrapped in `any()` to cover one *or*
many scope uris:

```cypher
WHERE $us IS NULL
   OR any(u IN $us WHERE
        node.uri = u
        OR node.uri STARTS WITH u + '/'
        OR node.uri STARTS WITH u + '#')
```

`$us` is `[vault]` (local default), `[under-uri]` (local `--under`), the `--vault`
list (remote), or `NULL` (remote `--profile` alone → all vaults). Correct for every
target type without knowing which it is:

| scope uri | matches |
|---|---|
| vault `my-notes` | the vault's docs + sections (`my-notes/...`) |
| folder `my-notes/projects` | everything under the folder |
| document `my-notes/foo.md` | the doc (`= u`) **and** its sections (`...foo.md#...`) |
| section `my-notes/foo.md#setup` | the section **and** its subsections (`#setup/...`) |

It runs as a **post-filter on the full-text hits** (the index already narrowed the
candidates), so it's a constant-factor cost on a small set — no extra scan, no
extra round trip.

---

## 6. Results and the external-stub nuance

Each result row carries:

| field | meaning |
|---|---|
| `label` | `Document` or `Section` |
| `uri` | address of the hit (feed to `ki get`) |
| `display_name` | title / heading text |
| `path` | absolute file path on the indexing machine |
| `content` | the matched node's body |
| `document_uri` / `document_title` | owning document (sections only; null for docs) |
| `score` | Lucene relevance |

### External stubs

Markdown links to **non-`.md` files or external URLs** become **stub Document
nodes** — metadata only, no indexed content. They exist so the graph can represent
"a real `.md` file points here," but they aren't first-class search targets.

The nuance:

- **Stub nodes are post-filtered out of results.** A stub has no `content`, so it
  has nothing of its own to rank, and surfacing a bare metadata node would be
  noise.
- **But the URL/reference is still found** — because it lives in the **`content`
  of the linking document or section** that cites it. A search for a URL (or any
  text near it) matches that *linking* node and surfaces there. So you don't lose
  the reference; it's attributed to the **doc that cites it**, not to the stub
  itself.

In other words: search the *citers*, not the *citation*. To then read the stub's
metadata directly, `ki get` its uri (copied from the linking doc's outline/links).

---

## 7. After the search: retrieval

Search returns addresses; pull the actual content by uri:

```sh
ki get --type full "<uri>" ["<uri>" ...]   # reconstructed reading-order body; batch URIs
ki get --type content "<uri>"              # node preamble + child pointers, to drill further
ki get --type path "<uri>"                 # metadata only (then read the file yourself)
```

`ki get` takes only document/section uris. For a folder, use `ki outline`.

---

## 8. Flag summary

| flag | meaning |
|---|---|
| `--types <csv>` | subset of `{document, section}` (default: both) |
| `--under <uri-or-path>` | **local** — narrow to a subtree of the vault you're in (folder / doc / section) |
| `--profile <name>` | **remote** — search this profile; alone → all its vaults |
| `--vault <uri,…>` | **remote** — limit to these vaults (requires `--profile`) |
| `-C, --directory <dir>` | resolve the local vault/profile as if run from `<dir>` |
| `--k <n>` | result cap (default 10) |
| `--json` | machine-readable rows (keys = the §6 fields) |

---

## 9. Worked examples

```sh
# Local — in ~/my-notes (bound to profile 'personal'); no flags needed:
ki search "rate limiting"                          # the whole vault you're in
ki search "rate limiting" --under ./api            # the api/ folder, by path
ki search "rate limiting" --under my-notes/api     # same, by uri
ki search "retry" --under my-notes/api/client.md   # one doc + its sections
ki search "retry" --types section --k 25           # sections only, wider cap

# Remote — name the profile (and optionally the vaults) explicitly:
ki search "rate limiting" --profile work                       # all vaults in 'work'
ki search "rate limiting" --profile work --vault api-docs      # one vault
ki search "incident" --profile work --vault api-docs,runbooks  # two vaults
```

---

## See also

- `docs/scoping.md` — the foundational profile / vault / scoping model (this doc is
  the search-specific deep dive on top of it).
- `docs/data-model/schema.md` — node labels, uri conventions, containment.

---

## Status

This doc describes the agreed search design. **Shipped:** the unified
Document+Section sweep, profile resolution with the stderr banner, and `--types`.
**Pending:** `--under` (the local subtree filter, §5.2), remote `--vault` scoping
(§5.3), and the `any()` / three-part scope predicate (§5.4) — documented here as the
target so the behavior is settled before the code lands. Current code applies scope
as a single `uri STARTS WITH <vault>/` prefix; this generalizes it to one-or-many
subtrees.
