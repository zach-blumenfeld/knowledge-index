# How `ki search` works

`ki search` is full-text retrieval over a vault's knowledge graph. It runs **one
ranked sweep** over the documents and sections in scope and returns the matching
*slices* — uri, title, score, and content — so an agent pulls just what it needs
instead of grepping and reading whole files.

This doc is the holistic picture: the index, the query, scope resolution
(including `--under`), result shaping, and the external-stub nuance.

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
  predicate, never a Lucene clause — see §4).
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

Every search resolves to **one profile** (which Neo4j to hit) and an **optional
scope** (which subtree of which vault). The resolution is computed fresh on every
invocation from *where you run the command* — there is **no stored "current
vault"** anywhere.

### 5.1 Profile is required

`ki search` never falls back to a default profile — it always tells you which one
it hit. Profile is resolved in order:

1. `--profile <name>`, else
2. the bound profile of the **vault you're in** (the `.ki/vault.yaml` at or above
   your working directory), else
3. **error** — nothing to search.

### 5.2 The vault you're in

"The vault you're in" = the nearest `.ki/vault.yaml` **at or above your working
directory**, found by walking up the tree (`find_vault_root`):

- **No `-C`** → start from the shell's cwd.
- **`-C <dir>`** → start from `<dir>` instead ("pretend my working dir is here").

That marker yields the vault's **uri** (its slug), its **root path** on disk, and
its **bound profile**. These three are the inputs to scoping. The resolved scope
is printed to stderr on every run, so it's never a silent guess:

```
ki: profile 'personal' · vault 'my-notes'   (from .ki)
```

### 5.3 Scope precedence

| You ran | Profile | Scope |
|---|---|---|
| inside a vault, no flags | the vault's bound profile | **that vault** |
| inside a vault, `--under X` | the vault's bound profile | **subtree `X`** within it |
| `--profile P` (anywhere) | `P` | **all vaults in `P`** |
| `--profile P --under <uri>` | `P` | **subtree `<uri>`** (the uri names the vault) |

Passing `--profile` deliberately drops the cwd-vault auto-scope: a vault lives in
exactly one profile, so the dir's vault is meaningless under a different one. That
is the "search across all vaults" mode.

### 5.4 `--under` — narrowing to a subtree

`--under` scopes a search to a containment subtree at **any level** — a whole
vault, a folder, a single document, or a section. It accepts **either a uri or a
filesystem path**, resolved by one function:

```python
def resolve_to_uri(arg, vault_uri, vault_path, cwd) -> str:
    # 0. all-vaults mode (--profile, no cwd vault): no anchor → must be a uri
    if vault_uri is None or vault_path is None:
        if arg.startswith(("/", "~", "./", "../")) or arg in (".", ".."):
            raise click.ClickException("--under <path> needs a vault; pass a uri")
        return arg.rstrip("/")

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

The three real behaviors:

- **A uri in this vault** (`my-notes/projects`, `my-notes/foo.md#setup`) →
  returned as-is.
- **A filesystem path** (`./projects`, `~/notes/projects`, an absolute path) →
  resolved against the vault root, slugified to match the index, returning the
  correct uri. Because the slug comes from the on-disk marker, this is
  **`-N`-safe**: if the directory is `my-notes/` but its slug collided and became
  `my-notes-1`, a *path* still resolves to `my-notes-1/...` correctly.
- **Neither** → a loud error, never a silent zero-result.

Two consequences worth internalizing:

- **The directory basename is not the uri** when a slug collision appended `-N`.
  So a *guessed* bare uri (`my-notes/...`) can silently mean nothing, whereas a
  *path* always resolves through the marker. Rule of thumb: copy uris from
  `ki search` / `ki outline` output; use a **path** when you mean "the folder I'm
  looking at."
- In **all-vaults mode** (`--profile`, no cwd vault) there's nothing to anchor a
  relative path against, so `--under` must be a **uri** there — which is also how
  you scope a vault you're *not* sitting in: `--profile work --under work-notes/api`.

### 5.5 The scope predicate

The resolved uri is **not** applied as a plain prefix. Containment in ki uris uses
two different separators:

- vault / folder / section → child: `/`
- document → its sections: `#`

…and a node's own uri has no trailing separator. So "node `X` and everything under
it" is a **three-part, type-agnostic** predicate:

```cypher
WHERE $u IS NULL
   OR node.uri = $u
   OR node.uri STARTS WITH $u + '/'
   OR node.uri STARTS WITH $u + '#'
```

This is correct for every target type without knowing which it is:

| `--under` target | matches |
|---|---|
| vault `my-notes` | the vault's docs + sections (`my-notes/...`) |
| folder `my-notes/projects` | everything under the folder |
| document `my-notes/foo.md` | the doc (`= $u`) **and** its sections (`...foo.md#...`) |
| section `my-notes/foo.md#setup` | the section **and** its subsections (`#setup/...`) |

It runs as a **post-filter on the full-text hits** (the index already narrowed the
candidate set), so the cost over the single-prefix form is a constant factor on a
small set — no extra scan, no extra round trip. `$u IS NULL` = unscoped
(all vaults).

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
| `--under <uri-or-path>` | scope to a containment subtree (vault / folder / doc / section) |
| `--profile <name>` | search this profile; drops cwd-vault auto-scope → all vaults |
| `-C, --directory <dir>` | resolve the vault/profile as if run from `<dir>` |
| `--k <n>` | result cap (default 10) |
| `--json` | machine-readable rows (keys = the §6 fields) |

---

## 9. Worked examples

```sh
# In ~/my-notes (bound to profile 'personal'):
ki search "rate limiting"                          # whole vault
ki search "rate limiting" --under ./api            # the api/ folder, by path
ki search "rate limiting" --under my-notes/api     # same, by uri
ki search "retry" --under my-notes/api/client.md   # one doc + its sections
ki search "retry" --types section --k 25           # sections only, wider cap

# From anywhere, across a whole profile:
ki search "rate limiting" --profile work                       # all vaults in 'work'
ki search "rate limiting" --profile work --under work-api/v2   # a subtree elsewhere
```

---

## Status

This doc describes the agreed search design. Recently shipped: the unified
Document+Section sweep, profile-required resolution with the stderr banner,
`--types`, and vault-level scoping. The `--under` flag, the `resolve_to_uri`
resolver (§5.4), and the three-part scope predicate (§5.5) are the **next**
implementation step — documented here as the target so the behavior is settled
before the code lands. Current code applies scope as a single `uri STARTS WITH
<vault>/` prefix; the three-part predicate generalizes it to any subtree.
