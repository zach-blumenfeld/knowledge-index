# Outline format for `ki outline`

The default output of `ki outline` is a table-of-contents-style render of the vault's containment hierarchy plus its cross-references. It is intended to be readable by **both humans and agents**: the eye scans display names on the left, and an agent can pick out URIs from a stable right-most column.

> The command was previously `ki tree`. `ki tree` is now a permanent alias for `ki outline` — same flags, same output. The format spec below uses `ki outline` throughout; replace mentally with `ki tree` if you're reading older transcripts.

This document defines the format only. Flag semantics (positional `<uri>`, `--depth`, back-compat `--at`) and the underlying Cypher (`B.12`) live in `docs/requirements_v01_mvp.md` and `docs/retrieval-queries.md` respectively.

`ki outline` writes the rendered format to stdout. To save it to a file, pipe (`ki outline > outline.txt`) — there is no separate output-format flag.

## Layout

### Header

Every `ki outline` invocation prints a two-line header before the tree:

1. **Key line** — one-letter type codes mapped to their labels.
2. **Column header** — `NAME`, `T`, `URI`.

Example:

```
Key:  V Vault   F Folder   D Document   S Section   L Links-to

NAME                                              T   URI
```

The header is always printed, even when the tree is empty. The key relies on the column header sitting one line below; do not insert a separator rule between them.

### Row format

Every node and every rendered edge gets one row:

```
<indent><name><space><dots> <T>   <URI>
```

- **`<indent>`** — two spaces per `:HAS` step from the rendered root. The root sits at indent 0.
- **`<name>`** — the node's display name. See *Name field* below for the per-label rule.
- **`<dots>`** — a dotted leader (`.`) filling the gap from the end of the name to the type column. The dot column lines up across rows; see *Column widths* below.
- **`<T>`** — single-letter type code (`V` / `F` / `D` / `S` / `L`).
- **`<URI>`** — the node's `uri` property. **Always the full URI**, never abbreviated — see *URI column* below.

### Type codes

| Code | Label    | Notes                                                                 |
|------|----------|-----------------------------------------------------------------------|
| `V`  | Vault    | Always the rendered root unless a positional URI (or `--at`) is given. |
| `F`  | Folder   | Trailing `/` on the name (`ideas/`) to visually mark it as a folder.  |
| `D`  | Document | Renders `displayName` (filename for internal md docs; link-text label for #37 stubs + external URLs). |
| `S`  | Section  | URI column shows the full section URI (no shorthand).                 |
| `L`  | Links-to | Renders an outbound `:LINKS_TO` edge from the row's parent.           |

## Name field

The name column carries enough information to identify the node without consulting the URI column.

| Label    | Rendering rule                                                                        |
|----------|---------------------------------------------------------------------------------------|
| Vault    | `name` (the vault directory basename).                                                |
| Folder   | `name + "/"`. Trailing slash is a visual hint, not part of the URI.                   |
| Document | `displayName`. For internal md docs that's the filename (same as `name` after #28). For #37 stubs and external URLs it's the link-text label written on first ingest — see `docs/data-model.md` *Three Document kinds*. The URI column carries the on-disk filename or URL alongside. |
| Section  | `displayName` (the heading text, not the slug).                                       |
| Links-to | `→ <relative-target-hint>` — see *LINKS_TO rendering* below.                          |

## URI column

The URI column carries the load-bearing identifier for the row. Every row shows the **full URI** — Vault, Folder, Document, Section, and LINKS_TO target alike. Agents and humans alike are expected to copy URIs from this column verbatim and feed them straight back into `ki outline <uri>` or `ki get <uri>`.

We tried a `#fragment` shorthand for sections earlier; it saved horizontal space but forced the reader to walk up the indented tree and concatenate ancestor slugs to reconstruct the real URI. Re-running `ki outline` rooted at a specific section is a common follow-up, and that flow shouldn't require any reconstruction.

The URI column is **never truncated**. If a URI exceeds the terminal width, it overflows past 80 columns (or wraps, depending on the terminal). This is a deliberate trade-off: clipping URIs would defeat the entire point of the right column.

## Ordering

Sibling rows are sorted in this order:

| Sibling kind                          | Sort key                                                            |
|---------------------------------------|---------------------------------------------------------------------|
| Folders + Documents under a parent    | Alphabetical by `name`. Folders before Documents on ties (rare).    |
| Sections under a Document             | `NEXT_SECTION` chain order (DFS reading order). Never alphabetical. |
| Sections under another Section        | `NEXT_SECTION` chain order.                                         |
| `:LINKS_TO` edges from a single source | Alphabetical by target URI. Stable across runs.                     |

The `B.12` Cypher in `docs/retrieval-queries.md` is responsible for surfacing `NEXT_SECTION` position; the renderer applies it as the sort key.

## LINKS_TO rendering

Outbound `:LINKS_TO` edges from a section render as **child rows of the source section**, prefixed with `→` and one extra indent step.

```
        Origins .................................. S   vault://abc-123/ideas/big-idea.md#big-idea/origins
          → Early Draft .......................... L   my-notes/refs/birth.md#big-idea/early-draft
        Implementation ........................... S   vault://abc-123/ideas/big-idea.md#big-idea/implementation
```

- The left-side hint is the target's `displayName` — heading text for Section targets, filename for Document targets, the markdown link text (e.g. `[Launch blog](https://...)` → `Launch blog`) for #37 external / stub targets. The full URI lives in the URI column on the same row, so the hint never needs to repeat any of it. Falls back to the literal `links_to` when no displayName is recorded (defensive — should be rare; surfaces as a visible breadcrumb that the target is missing a label).
- LINKS_TO rows never have a description sub-line (they are edges, not nodes).
- An `L` row counts as a `:HAS` step for indent purposes (it is rendered as a child of its source) but does not extend the tree — its target node is not expanded inline. To follow a link, the agent re-invokes `ki outline "<Label>:<uri>"`.

Rendering LINKS_TO inline rather than cross-branching is a deliberate v1 simplification — Rich's `Tree` does not support cross-branch references, and inline rendering preserves the column-aligned ToC feel.

## Section URI format

Section URIs in the URI column show the **full URI**, including the doc URI and the full heading-path fragment:

```
    big-idea.md .................................. D   vault://abc-123/ideas/big-idea.md
      Big Idea ................................... S   vault://abc-123/ideas/big-idea.md#big-idea
        Background ............................... S   vault://abc-123/ideas/big-idea.md#big-idea/background
        Origins .................................. S   vault://abc-123/ideas/big-idea.md#big-idea/origins
```

The fragment for a nested heading is the **full heading path** (`<h1-slug>/<h2-slug>/...`), not just the leaf — this matches the on-disk `Section.uri` exactly. This means deeply nested sections under a long heading produce long URIs. We accept the verbosity because:

1. The URI is copy-pasteable straight into `ki outline <uri>` and `ki get <uri>`.
2. The alternative — leaf-only shorthand or `#fragment` shorthand — requires the reader to walk up the indented tree and concatenate ancestor slugs to reconstruct anything. That's the most common follow-up flow, so it shouldn't cost the user a manual step.
3. When the heading slugs are long it's typically a sign the user wrote long headings; truncating their slugs in the display would only hide the cost, not avoid it.

## Truncation

The name column has a hard cap (default 48 characters, including indent). Any row whose `indent + name` would exceed the cap is truncated with a trailing `…`:

```
    exploring-multi-modal-retrieval-and-rerank…    D   vault://abc-123/ideas/exploring-multi-modal-retrieval-and-rerankers.md
      Implementation details for the section-tr…   S   #implementation-details-for-the-section-tree-builder
```

Rules:

- Truncation operates on the **whole left side as one string** (indent + rendered name).
- A truncated row drops its dotted leader (the cap is already reached) and uses a single space before the type column. The type column still lines up.
- The URI column is **never** truncated; only the name side is.
- `--full --no-truncate` (TBD) disables the cap entirely and lets long names extend past the type column — useful for piping to a pager, hostile to scanning.

### Column widths

The name column width is computed once per render as `min(48, max(indent + name_length over all rows) + small pad)`. Wider terminals do **not** get a wider name column by default — the cap holds. We may make this `$COLUMNS`-aware later.

## Wire record format (B.12 → renderer)

`ki outline` is built on `B.12` from `docs/retrieval-queries.md`. B.12 returns a flat row stream; the renderer assembles the tree client-side. The row schema is the contract between the two.

| Field         | Type                                  | Notes                                                                                                                              |
|---------------|---------------------------------------|------------------------------------------------------------------------------------------------------------------------------------|
| `depth`       | int                                   | 0 at the rendered root, +1 per `HAS` step. Renderer turns `depth → "  " * depth` for indent.                                       |
| `inrel`       | `"HAS"` \| `"LINKS_TO"` \| `null`     | The inbound edge that brought us to this row. `null` only for the rendered root.                                                   |
| `label`       | `"Vault"` \| `"Folder"` \| `"Document"` \| `"Section"` | The node's true label. **Not** the rendered type letter — that's derived (see below).                          |
| `name`        | string                                | The node's `name` property (filename / heading-slug / dir-basename / vault-basename). Drives alphabetical sort for F/D/L siblings. |
| `displayName` | string                                | The node's `displayName` property. Drives the *name field* on the rendered row.                                                    |
| `uri`         | string                                | The node's full `uri`. The renderer prints it verbatim in every row's URI column — no abbreviation.                                 |
| `parent_uri`  | string \| `null`                      | URI of the parent node along the inbound edge. `null` for the root. Used by the renderer to group siblings.                        |
| `sort_pos`    | int \| `null`                         | `NEXT_SECTION` position within the section's parent document. Non-null only for `Section` rows that came through `HAS`. Drives section sibling order. |

### How the renderer derives display

- **Type letter:** `"L"` when `inrel == "LINKS_TO"`, else `label[0]` (`V` / `F` / `D` / `S`).
- **URI column:** always the wire `uri`, verbatim, regardless of `label` or `inrel`.
- **`→` prefix on the name:** only when `inrel == "LINKS_TO"`.
- **Indent:** `"  " * depth`.

### Sibling ordering (client-side)

Sibling groups are formed by `parent_uri`. Within each group:

| Sibling kind                                              | Sort key                          |
|-----------------------------------------------------------|-----------------------------------|
| `inrel == "HAS"` and `label in {"Folder", "Document"}`    | alphabetical by `name`            |
| `inrel == "HAS"` and `label == "Section"`                 | ascending `sort_pos` (NEXT_SECTION) |
| `inrel == "LINKS_TO"`                                     | alphabetical by `uri` of target   |

A parent's children always partition cleanly into at most two of these groups:
- **Folder/Vault parents** have only HAS-Folder/Document children (alphabetical group).
- **Document and Section parents** have HAS-Section children (NEXT_SECTION group) and LINKS_TO children (alphabetical-by-uri group). The renderer emits the HAS-Section group first, then the LINKS_TO group.

This partitioning is a consequence of the data model, not a renderer rule — Folders/Vaults never source LINKS_TO; Documents/Sections never directly parent Folders.

### Multi-root (no `--at`)

When `ki outline` is invoked without a positional URI (or back-compat `--at`), there is no single root URI. The query falls back to matching **every `:Vault` in the graph** as a root, and the walk fans out from each. The wire format is unchanged — multiple rows arrive with `depth = 0, parent_uri = null`.

The renderer treats the `parent_uri = null` group as the implicit "root group," sorts alphabetically by `name`, and DFS-emits each vault tree in turn. There is no separator between vaults in the rendered output — the `V` row at depth 0 is the visual boundary.

```
vault-one ........................... V   vault://abc
  ideas/ ............................. F   vault://abc/ideas
    big-idea.md ...................... D   vault://abc/ideas/big-idea.md
      ...
vault-two ........................... V   vault://def
  research/ .......................... F   vault://def/research
    notes.md ......................... D   vault://def/research/notes.md
      ...
```

The single-root case (a positional URI given, or back-compat `--at <Label>:<uri>`) is just the degenerate version of this: the `parent_uri = null` group has exactly one entry, and the renderer takes the same code path.

Multi-user note: today there is only one user, so "all vaults" is unambiguous. When multi-user becomes real, the query will need to scope to the current user's vaults via `:USES_VAULT` — tracked as a follow-up, not blocking phase 3.

### Renderer pseudocode

The renderer's job is: take B.12's flat row stream + B.12-links' edge rows, merge them, sort sibling groups, and emit in DFS order.

```python
def build_tree(root_uri: str | None, depth: int) -> list[Row]:
    hier_rows = run_b12(root_uri, depth)             # may have many roots if root_uri is None
    ds_uris = [r.uri for r in hier_rows if r.label in ("Document", "Section")]
    raw_links = run_b12_links(ds_uris)

    # B.12-links returns target details + parent_uri. Renderer assigns depth
    # and inrel from the (already-known) parent depth.
    depth_by_uri = {r.uri: r.depth for r in hier_rows}
    link_rows = [
        Row(
            depth=depth_by_uri[lr.parent_uri] + 1,
            inrel="LINKS_TO",
            label=lr.label, name=lr.name, displayName=lr.displayName,
            uri=lr.uri, parent_uri=lr.parent_uri,
            sort_pos=None,
        )
        for lr in raw_links
    ]

    all_rows = hier_rows + link_rows
    children_by_parent: dict[str | None, list[Row]] = group_by(all_rows, key=lambda r: r.parent_uri)

    # Apply per-group sort rules.
    for parent_uri, kids in children_by_parent.items():
        if parent_uri is None:
            # Root group: one vault per row (no <uri>) or one root row (with <uri>).
            # All entries here are Vaults (or the single specified root). Sort by name.
            kids.sort(key=lambda k: k.name)
            continue
        has_others   = [k for k in kids if k.inrel == "HAS" and k.label != "Section"]
        has_sections = [k for k in kids if k.inrel == "HAS" and k.label == "Section"]
        link_kids    = [k for k in kids if k.inrel == "LINKS_TO"]
        has_others.sort(key=lambda k: k.name)
        has_sections.sort(key=lambda k: k.sort_pos)
        link_kids.sort(key=lambda k: k.uri)
        children_by_parent[parent_uri] = has_others + has_sections + link_kids

    # DFS emit. Start from the root group; recurse into each node's children.
    output: list[Row] = []
    def emit(node_uri: str) -> None:
        for child in children_by_parent.get(node_uri, []):
            output.append(child)
            emit(child.uri)

    for root in children_by_parent.get(None, []):
        output.append(root)
        emit(root.uri)
    return output
```

Key points:
- The `parent_uri = None` group is the only place vaults appear when no root URI is given; the same path also handles the single-root case (positional `<uri>` or `--at` given) without a special branch.
- LINKS_TO rows are synthesized client-side. Their `depth` is `parent_depth + 1`; B.12-links never returns a depth because it doesn't know where its sources live in the tree.
- Sort is applied **once per sibling group**, not as a global ORDER BY. Sibling rules vary by group composition (alphabetical for F/D, NEXT_SECTION for sections, alphabetical-by-URI for links).
- DFS-emit walks `children_by_parent` recursively. HAS is a single-parent tree (no cycles by construction), but the merged HAS + LINKS_TO map *can* cycle — e.g. a section that links back to an ancestor. The renderer guards with a `visited` set on URI so the walk always terminates. The L-row itself still renders (the link is visible in the outline); the renderer simply won't re-expand the target's subtree under it.

### Worked example

For the rendered output:

```
my-knowledge-base ............................... V   vault://abc-123
  ideas/ ......................................... F   vault://abc-123/ideas
    big-idea.md .................................. D   vault://abc-123/ideas/big-idea.md
      Big Idea ................................... S   vault://abc-123/ideas/big-idea.md#big-idea
        Origins .................................. S   vault://abc-123/ideas/big-idea.md#big-idea/origins
          → Early Draft .......................... L   my-notes/refs/birth.md#big-idea/early-draft
```

the wire rows are (in render order):

```
{depth: 0, inrel: null,       label: "Vault",    name: "my-knowledge-base", displayName: "my-knowledge-base", uri: "vault://abc-123",                                              parent_uri: null,                                       sort_pos: null}
{depth: 1, inrel: "HAS",      label: "Folder",   name: "ideas",             displayName: "ideas",             uri: "vault://abc-123/ideas",                                         parent_uri: "vault://abc-123",                          sort_pos: null}
{depth: 2, inrel: "HAS",      label: "Document", name: "big-idea.md",       displayName: "Big Idea",          uri: "vault://abc-123/ideas/big-idea.md",                             parent_uri: "vault://abc-123/ideas",                    sort_pos: null}
{depth: 3, inrel: "HAS",      label: "Section",  name: "big-idea",          displayName: "Big Idea",          uri: "vault://abc-123/ideas/big-idea.md#big-idea",                    parent_uri: "vault://abc-123/ideas/big-idea.md",        sort_pos: 0}
{depth: 4, inrel: "HAS",      label: "Section",  name: "big-idea/origins",  displayName: "Origins",           uri: "vault://abc-123/ideas/big-idea.md#big-idea/origins",            parent_uri: "vault://abc-123/ideas/big-idea.md#big-idea", sort_pos: 2}
{depth: 5, inrel: "LINKS_TO", label: "Section",  name: "early-draft",       displayName: "Early Draft",       uri: "vault://abc-123/refs/birth.md#early-draft",                     parent_uri: "vault://abc-123/ideas/big-idea.md#big-idea/origins", sort_pos: null}
```

Notes on the example:
- The root row has `inrel: null`, `parent_uri: null`, `sort_pos: null`.
- Section URI fragments encode the **full heading path** (`<h1-slug>/<h2-slug>/...`), not just the leaf. So `Origins` (an H2 under `# Big Idea`) has fragment `#big-idea/origins`, mirroring the parent-child heading nesting. This matches the ingested `Section.uri` exactly.
- The Big Idea section has `sort_pos: 0` because it's the first section in `big-idea.md`'s NEXT_SECTION chain. Origins is at `sort_pos: 2` because the doc's chain is `Big Idea (0) → Background (1) → Origins (2) → Implementation (3) → Appendix (4)` — even though Background isn't shown above (`--depth` cut, or just elided in the example).
- The LINKS_TO row's `label` is `"Section"` (its target is a section in `refs/birth.md`), but the renderer prints `L` in the type column because `inrel == "LINKS_TO"`.

## `--full` flag

`--full` adds a description sub-line under each **Vault** row:

```
my-knowledge-base ............................... V   vault://abc-123
    > My personal knowledge base for ideas, research, and projects.
  ideas/ ......................................... F   vault://abc-123/ideas
```

- The sub-line is indented one extra level past the parent name and prefixed with `>`.
- Multi-line stored descriptions are joined to a single line and truncated to terminal width with `…`.
- Folders never have descriptions (per `:Folder` schema — no `description` property).
- **Document and Section descriptions are not surfaced in v1**, even under `--full`. The cost-benefit (vertical noise vs. occasional usefulness) didn't land — to read a doc's or section's full content, use `ki search` with the URI.

A future flag (`--full --no-truncate`, or `--describe`) may expand the description treatment.

## What's intentionally not in the output

### `lastSeenAt` / `firstSeenAt`

The node properties exist in the graph but are **not** surfaced in `ki outline`. Reason: `ki` is an index, not a storage layer. `lastSeenAt` reflects when `ki index` last touched the node — it does **not** reflect when the user edited the source file. Surfacing it as an "updated" column would be deceptive: a vault that was just re-indexed would show every node "updated today" regardless of file mtime.

If a future need for source-file mtime emerges, the fix is to read it from the file system at ingest and store it as a separate property (`sourceMtime`), then surface that — not to repurpose `lastSeenAt`.

### Section descriptions, Document descriptions, aliases

Surfacing these inline would force every row to wrap or grow vertically, breaking the ToC scan. They are accessible via `ki search` on the URI.

### Backlinks

`ki outline` shows only **outbound** `:LINKS_TO` edges. Inbound links (backlinks) are a known gap — `ki search --type neighbors` is being removed in 0.4.0 and there is no CLI surface for B.9 yet. Tracked in #35.

## Open questions tracked elsewhere

- Multi-user scoping for the no-root-URI case — needs a `:USES_VAULT` filter once multi-user becomes real. Not blocking phase 3.
