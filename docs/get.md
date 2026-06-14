# How `ki get` works

`ki get <uri> [<uri> …]` fetches a node's **metadata and content by uri**. It's the
retrieval end of the loop: `ki search` and `ki outline` hand you uris (and optionally some content in the case of `ki search`); `ki get`
turns a uri into the actual text. For the profile/vault model it shares with the
other read commands, see `docs/scoping.md`; for `search`, `docs/search.md`.

---

## 1. What it takes, what it returns

- **Input:** one or more **Document or Section uris** (batch as many as you like).
- **Output:** a metadata **shell** per uri, plus `content` controlled by `--type`.

Only `:Document` and `:Section` uris are valid — those are the nodes that carry
text. A `:Folder` or `:Vault` uri is a clear error with a redirect:

```
error: ki get is for text retrieval but you passed a Folder (my-notes/ideas).
       Use 'ki outline my-notes/ideas' to enumerate contents recursively under folder.
```

Use `ki outline` to enumerate a folder/vault; `ki vault list` to see vaults.

---

## 2. The three `--type` levels

`--type` controls how much content rides along on the metadata shell:

| `--type` | Content returned | Use when |
|---|---|---|
| `path` | none (shell only — `uri`, `name`, `path`, …) | you just want the on-disk path / metadata, then read the file yourself |
| `content` *(default-ish)* | the node's **own** `content` — its preamble + `uri:` pointers to direct children | you want a shallow view and may drill further |
| `full` | the **reconstructed reading-order body** of the whole subtree | you want everything under the node in one shot |

The **`content`** level reflects ki's *Content Construction Rules* (see
`docs/data-model.md`): a node stores only the body text directly under its own
heading, followed by `uri:` pointer lines to its direct children. So `--type
content` is shallow by design — child body text isn't included; you see pointers
and can `ki get` those next.

The **`full`** level does that reconstruction for you, server-side, in one query
(no client-side recursion):

- **Document** → its preamble followed by **all its sections in `NEXT_SECTION`
  reading order**.
- **Section** → the section **and its descendant sections**, in reading order.

```sh
ki get --type path    "my-notes/api/client.md"          # metadata only
ki get --type content "my-notes/api/client.md"          # preamble + child pointers
ki get --type full    "my-notes/api/client.md"          # whole document, reading order
ki get --type full    "my-notes/api/client.md#retries"  # one section + its subsections
```

### The drill pattern

`--type content` then follow pointers, vs. `--type full` in one shot:

```sh
ki get --type content "my-notes/api/client.md"   # see the section pointers…
ki get --type full    "my-notes/api/client.md#auth/token-refresh"   # …then pull the one you want
```

Reach for `content` + drill when a document is large and you only need one branch;
reach for `full` when you want the whole thing.

---

## 3. Batching

Pass multiple uris in one call — each is fetched independently and rendered in
order, separated by a `---` rule (text) or as a `results` array (JSON):

```sh
ki get --type full "my-notes/api/client.md#retries" "my-notes/api/server.md#limits"
```

A uri that doesn't resolve becomes an **error entry**, not a crash — the other
uris still return. Exit code is `0` only when every uri resolved; `1` if any
errored (errors go to stderr in text mode, into an `errors` array in JSON).

---

## 4. Output shape

The metadata shell common to every result: `uri`, `label`, `name`, `displayName`,
`path`, `aliases`, `content`. Plus, by label:

| label | extra fields |
|---|---|
| `Document` | `frontmatter`, `sourceType`, `firstLoadedAt`, `lastLoadedAt` |
| `Section` | `headingLevel` |

`--json` emits `{ "type": <level>, "results": [...], "errors": [{uri, message}] }`.
Text mode prints a metadata header per result, then the content block, with `path`
echoed so you can fall back to reading the file directly.

### External stubs

A stub Document (an external URL or non-`.md` link target — `sourceType =
URL_LINK`) has **no `content`**. `ki get` on a stub returns just its metadata
(`path`/`displayName`); `--type content`/`full` come back empty. That's expected —
the stub exists as a link target, not as indexed text (see `docs/search.md` §6).

---

## 5. Scoping

`ki get` resolves a **profile** the same way the other read commands do
(`docs/scoping.md` §4): `--profile`, else the bound profile of the vault you're in
(cwd, or `-C <dir>`), else the config default. It does **not** take `--under` /
`--vault` — a uri is already a fully-qualified address (it names its vault), so:

- **Local** — in a vault, no flags: the bound profile is used; pass any uri.
- **Remote** — `--profile P`: fetch uris from profile `P` without being in the
  vault (the uri identifies the node; the profile says which Neo4j).

Because a uri is self-addressing, a single `ki get` can pull uris from **any vault
in the resolved profile** — there's no per-call vault lock.

---

## See also

- `docs/scoping.md` — profiles, vaults, and how the read commands resolve them.
- `docs/search.md` — `ki search` (returns the uris you feed here).
- `docs/outline-format.md` — `ki outline` (the other uri source).
- `docs/data-model.md` — *Content Construction Rules* (the `content` + `uri:`
  pointer model that `--type content`/`full` read and reconstruct).
