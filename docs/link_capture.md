# Document link capture

How `ki` turns markdown links into graph edges and target nodes. This page is the behavior reference — if a link form doesn't appear here, ki either drops it silently (rare; see *What ki does not capture* below) or it falls into one of the documented buckets.

Related docs:
- `docs/data-model.md` *Three Document kinds* — the node shapes that target nodes take.
- `docs/index_rm_behavior.md` — what happens to link targets on re-index and `ki drop`.
- `docs/ingest-cypher.md` §4.3 step 5.5 / 5.6 — the underlying MERGE Cypher.

## What `ki` recognizes

Three syntactic forms in markdown body text and section bodies:

| Markdown | Form | Notes |
|---|---|---|
| `[[Target]]` or `[[Target\|Display]]` | Obsidian-style wikilink | Resolved against names + aliases of indexed `.md` Documents in the same vault. |
| `![[Target]]` | Obsidian-style embed | Same resolution as wikilink; the `embed=true` property is set on the resulting `:LINKS_TO` edge. |
| `[text](href)` | CommonMark markdown link | Captures **every** target — `.md` paths, non-md paths, URLs, mailto, obsidian://, anything. |

`![alt](url)` — markdown image syntax — is excluded. Images aren't links in our model.

`[click](#anchor)` — pure-fragment links (same-doc anchors) — are skipped. The fragment text is in the source section's content already; there's no useful graph edge to emit.

## Link classification

Every captured `[text](href)` is classified into one of four kinds. The classifier lives at `src/ki/parser/markdown.py:_classify_link`. The kind drives ingest-time routing.

| Kind | Detection | Example |
|---|---|---|
| `wikilink` | `[[...]]` syntax (with or without `!` embed prefix, with or without `\|display`) | `[[Big Idea]]`, `[[Doc#Section\|Anchor]]`, `![[image]]` |
| `md_link` | `[text](href)` where `href` (minus optional `#fragment` and `?query`) ends in `.md` (case-insensitive) | `[click](./other.md)`, `[ref](notes/foo.md#section)` |
| `non_md_file` | `[text](href)` with no URL scheme and the trimmed `href` doesn't end in `.md` | `[Slides](./deck.pptx)`, `[Diagram](../arch.png)` |
| `external_url` | `[text](href)` where `href` matches `^[a-z][a-z0-9+\-.]*:` (any RFC-3986-style scheme) | `[Blog](https://...)`, `[me](mailto:me@x.com)`, `[file](obsidian://open?vault=v&file=f)` |

The scheme regex catches `https`, `http`, `mailto`, `ftp`, `obsidian`, `file`, and anything else that follows the standard scheme grammar. We don't allowlist — that would just be a list to maintain.

`href` is URL-decoded **only for the path kinds** (`md_link`, `non_md_file`) so `./my%20deck.pptx` resolves to `./my deck.pptx` on disk. External URL hrefs are kept verbatim (no normalization in 0.4.0 — `https://foo.com/bar` and `https://foo.com/bar/` are different nodes; see *No URL normalization* below).

## How each kind resolves

The pipeline routes by `kind` at `src/ki/ingest/pipeline.py:_process_link`.

### `wikilink` (and `md_link`)

Goes through the in-memory `WikilinkResolver` (`src/ki/ingest/pipeline.py:WikilinkResolver`). The resolver is populated from indexed `.md` Documents in the **current vault** — by name (case-insensitive, with/without `.md`) and by alias. Cross-vault wikilink resolution is out of scope for v1.

- `[[Big Idea]]` → looks up `big idea` and `big-idea` in name keys, and `big idea` in alias keys → resolves to `Document.uri` like `my-notes/notes/big-idea.md`.
- `[[Big Idea#Origins]]` → resolves the `Big Idea` part to a doc URI, then appends `#origins` (slugified) → resolves to a `Section.uri`.
- `[click](./other.md)` → same resolver, treats `./other.md` as a name lookup (`./other.md`, `./other`, `other` all tried) → resolves to a Document.

If resolution fails, the link is silently dropped (no edge, no node). v1 does not create `WIKILINK_UNRESOLVED` stubs for failed wikilinks — that's a deferred concept in the schema.

The resulting `:LINKS_TO` edge has `wikilink = true, embed = (true if `![[...]]`)`.

### `non_md_file`

A markdown link to a non-md path. ki tries to resolve the path on disk; the outcome depends on where the resolved path lands.

**Algorithm** (`src/ki/ingest/pipeline.py:_resolve_non_md_link`):

1. Strip optional `#fragment` and `?query` from the href.
2. Resolve the path:
   - Absolute path (`/Users/...`) → use as-is.
   - Relative path (`./deck.pptx`, `../foo.png`) → resolve against the source markdown file's directory.
3. Compute the absolute on-disk path (`.resolve()`).
4. Branch:
   - **Inside the vault root and the file exists on disk** → MERGE a stub `:Document` (`sourceType=LOCAL_STUB`) with `path`, `fileHash = sha256(file bytes)`, and `displayName = first link text seen`. Materialize any missing parent `:Folder` chain. Add a `:LINKS_TO` edge from the source.
   - **Inside the vault root but the file does NOT exist on disk** → log a warning, skip. No node created, no edge emitted. The link text still lives in the source section's content for fulltext recovery if the file shows up later.
   - **Outside the vault root** → treat as external. MERGE an external `:Document` with URI `file:///absolute/path` and `displayName = first link text seen`. No `path`, no `fileHash`, no `HAS` edge. (Per #37 design — vault-escaping links land in the external bucket rather than the broken-link bucket.)

The resulting `:LINKS_TO` edge has `wikilink = false, embed = false`.

### `external_url`

Any href with a URL scheme. ki MERGEs an external `:Document` keyed by the URL string **as-is** (no normalization — see *No URL normalization* below) and sets `displayName = first link text seen`.

External Documents have:
- `sourceType = URL_LINK`
- `uri = the URL string` (or `file:///...` for vault-escaping non-md links from the previous bucket)
- `displayName = the markdown link text` from the first source that introduced this URL (`ON CREATE SET` — sticky across re-ingests)
- No `path`, no `fileHash`, no incoming `:HAS` edge

The single-parent `:HAS` invariant explicitly **does not apply** to external Documents (see `docs/data-model.md` §4.2). External Documents live outside the containment tree and are reachable only via `:LINKS_TO`.

Cross-vault collapse comes for free: two vaults that link the same URL share one external `:Document` node with `:LINKS_TO` edges from both.

The resulting `:LINKS_TO` edge has `wikilink = false, embed = false`.

## `displayName` precedence

The `[text]` in `[text](href)` becomes the target's `displayName` — for non-md stubs and externals, set via `ON CREATE SET` so the first ingest that introduces the URI "wins" the slot. Subsequent ingests (re-indexes, other vaults linking the same URL) **do not overwrite** displayName. They contribute their link text to the target's `aliases` list instead, via the same `WRITE_DISPLAY_TEXT_ALIASES` step that wikilink display texts use.

| Scenario | displayName | aliases |
|---|---|---|
| `[Launch blog](https://...)` (first time seeing this URL) | `Launch blog` | `[]` |
| Same vault has `[Launch blog](https://...)` AND `[Aura announcement](https://...)` (same URL, two sections) | `Launch blog` *(first seen wins)* | `[Aura announcement]` |
| Vault A indexed first with `[Original Name](https://...)`, then vault B indexed with `[Other Phrasing](https://...)` | `Original Name` *(stuck — first vault wins)* | `[Other Phrasing]` |
| `[](https://...)` — no link text | URL itself (fallback) | `[]` |

The same rule applies to internal non-md stubs (`[Q3 deck](./deck.pptx)` → displayName = `Q3 deck`, name stays `deck.pptx`).

For internal `.md` Documents the displayName always equals the filename (per #28); link-text aliasing flows through the same channel but is conventionally not used to "rename" the doc.

## What surfaces where

| Surface | Sees external URLs? | Sees internal non-md stubs? |
|---|---|---|
| `ki outline` | Yes, as `D` rows (no children — external Documents have no HAS subtree); LINKS_TO targets render with `displayName` as the left-side hint. | Yes, as `D` rows under their parent folder. |
| `ki search --types document` | Yes — external Documents are in the `content_search` fulltext index via `displayName + aliases`. | Yes. |
| `ki search --types section` | LINKS_TO edges affect ranking indirectly via section content (which still contains the raw markdown), but external Documents themselves are not sections. | Same. |
| `ki get <uri>` | Yes — pass an external URL and get the Document's metadata. | Yes — pass the stub's URI. `--type full` and `--type content` are no-ops for stubs and externals since they have no `content`. |
| `ki drop <vault>` | Survives if any other vault still links to it. Otherwise GC'd by the orphan-sweep step in the remove routine (see `docs/index_rm_behavior.md` *Removal routine* step 3). | Removed with the vault (HAS-attached). |

## Re-ingest behavior

Re-indexing a vault nukes the vault's content first and re-ingests from scratch (`docs/index_rm_behavior.md`). For links:

- **Wikilinks / md_links** to other content in the same vault: resolver is rebuilt from the surviving graph, so resolution is consistent.
- **Internal non-md stubs** in the re-indexed vault: removed during the nuke step (they were HAS-attached to the vault). Re-created on the new ingest if the markdown link is still present.
- **External Documents** (URLs and `file:///` out-of-vault): survive the nuke (no HAS edge to the vault). On re-ingest, `LINKS_TO` edges are recreated from the new content. If the URL is no longer linked from anywhere (the re-indexed vault was its sole referrer), the orphan-GC step removes the external Document. If other vaults still link to it, it stays.
- **displayName** on a surviving external Document is **not** overwritten by the re-ingest's link text — first-seen wins via `ON CREATE SET`. Wipe the external Document (or `ki nuke`) to reset it.

## No URL normalization (v1)

URLs are stored verbatim. We do **not** normalize:

- Trailing slashes — `https://foo.com/bar` ≠ `https://foo.com/bar/`
- Query strings — `https://foo.com/x?utm_source=a` ≠ `https://foo.com/x`
- Fragments — `https://foo.com/x#a` ≠ `https://foo.com/x#b`
- Case — `https://Foo.com` ≠ `https://foo.com`

Same string → same node (cross-vault collapse). Different string → different nodes. The identity is whatever the user wrote in their markdown.

This is a deliberate v1 punt — every normalization rule is a fresh debate (strip `?utm_*`? strip trailing `/`? lowercase the host but not the path?). We'd rather see how real vaults look before committing to a rule. Track via [#37](https://github.com/zach-blumenfeld/knowledge-index/issues/37) if needed.

## What `ki` does not capture

- **Filesystem-walk discovery for non-md files.** Stub `:Document` nodes are link-driven only — a `.pptx` file in your vault that no markdown links to does not appear in the graph. Vaults often contain `.DS_Store`, build artifacts, etc.; walking the whole tree would create noise.
- **Wikilinks to non-md files.** `[[some-file.pdf]]` is treated as a wikilink and tries to resolve via name/alias; if the resolver doesn't find a Document by that name, the link is silently dropped. To pull a non-md file into the graph, link it with markdown syntax: `[Spec](./spec.pdf)`.
- **Auto-fetched titles or descriptions for external URLs.** ki does not hit the network at ingest. The link text is the only displayName signal.
- **Backlinks.** ki captures outbound `:LINKS_TO` edges only. Backlinks (`who links TO this?`) are tracked in [#35](https://github.com/zach-blumenfeld/knowledge-index/issues/35).
- **Persistent never-reuse for external Documents.** When an external Document loses all its LINKS_TO edges (orphan), the next removal-routine pass GC's it. If the URL is later re-linked, a fresh `:Document` is created — there's no tombstone history (matches the slug-reuse behavior documented in `docs/data-model.md`).

## Worked example

Source file `~/blog/post.md`:

```markdown
# A post

See [Launch blog](https://neo4j.com/blog/agentic-ai/) for details.
The [Q3 deck](./assets/deck.pptx) has the numbers.
Compare with [[other-post]] — and check the [old draft](./drafts/legacy.md)
plus [the external slides](../company-shared/quarterly.pptx).
The [missing thing](./does-not-exist.md) is just dropped.
```

Assuming the vault is `~/blog` (slug `blog`), `~/blog/assets/deck.pptx` exists, and `~/company-shared/quarterly.pptx` exists outside the vault, this single section produces:

| Kind | Target URI | Notes |
|---|---|---|
| `external_url` | `https://neo4j.com/blog/agentic-ai/` | External `:Document`, displayName = `Launch blog`. |
| `non_md_file` (inside vault, exists) | `blog/assets/deck.pptx` | Internal stub `:Document`, HAS-attached to `:Folder {uri: "blog/assets"}` (materialized fresh), displayName = `Q3 deck`, fileHash = sha256 of bytes. |
| `wikilink` | `blog/notes/other-post.md` *(if `other-post.md` exists in the vault)* | Resolved via name lookup. |
| `md_link` (inside vault, exists) | `blog/drafts/legacy.md` *(if `legacy.md` exists)* | Resolved via name lookup of `./drafts/legacy.md`. |
| `non_md_file` (outside vault) | `file:///Users/zach/company-shared/quarterly.pptx` | External `:Document` (no HAS edge), displayName = `the external slides`. |
| `non_md_file` (missing) | — | Warning logged: *"link target does not exist on disk … skipping"*. No node, no edge. |

Five `:LINKS_TO` edges total, four target Documents created (one already existed via `wikilink`).

## Where the code lives

| File | Responsibility |
|---|---|
| `src/ki/parser/markdown.py` | `_LINK_RE`, `_WIKILINK_RE`, `_EMBED_RE`; `_classify_link`; `_extract_links` builds `ParsedLink` rows. |
| `src/ki/ingest/pipeline.py` | `_build_links_to_rows`, `_process_link`, `_resolve_non_md_link`, `_record_external`. Threads the source file's on-disk Path through `pending_links` so relative paths resolve. |
| `src/ki/ingest/queries.py` | `WRITE_STUB_DOCUMENTS`, `WRITE_EXTERNAL_DOCUMENTS`, `WRITE_LINKS_TO`, `WRITE_DISPLAY_TEXT_ALIASES`. |
| `docs/data-model.md` | Document kinds matrix; HAS-invariant amendment; displayName precedence rule. |
| `docs/ingest-cypher.md` | Write-order narrative for steps 5.5 / 5.6 / 6 / 7. |
| `docs/index_rm_behavior.md` | What happens to internal stubs vs external Documents on re-index and `ki drop`. |
