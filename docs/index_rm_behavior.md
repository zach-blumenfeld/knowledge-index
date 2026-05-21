# Index / remove behavior — vault-level sync

This page is the authoritative behavior spec for `ki index`, `ki rm`, and `ki nuke` in 0.4.0. Read this before changing any of those commands or their underlying queries.

> **Vocabulary.** We say **remove** when we mean "take a node or vault out of the index." Cypher keywords (`DELETE`, `DETACH DELETE`) stay verbatim — they're a language reserved word — but every user-facing string, error message, doc paragraph, and code comment uses "remove."

## Mental model

`ki` keeps the **vault** as the only unit of sync. The graph mirrors what's on disk *at the vault granularity* — never at the document, section, or folder level. If a file disappears between two `ki index` runs, the only way the index reflects that is by re-indexing the whole vault.

This is a deliberate v0.4.0 simplification:

- One way to **add / refresh** content: `ki index <vault>`.
- One way to **remove** content: `ki rm <vault>`.
- One way to **wipe everything**: `ki nuke`.

Document-level / subtree-level granularity is **not** exposed in 0.4.0. If a user asks for it, the answer is "re-index the vault." This keeps state-reconciliation logic out of the per-document code path and makes the graph easy to reason about — at any moment, every indexed Document, Section, and Folder mirrors a file that existed on disk the last time the vault was indexed.

## `ki index <vault>`

**On a fresh vault (no `.ki/vault.yaml`, no matching `:Vault` in the graph):** standard first-time ingest. Compute a slug from the directory basename, write `.ki/vault.yaml`, ingest everything.

**On an already-indexed vault:** treat it as a full re-sync.

1. Read the existing `.ki/vault.yaml`. The `uri:` field identifies the existing `:Vault` in the graph.
2. **Remove the vault contents in the graph** (Documents, Sections, Folders, HAS edges, LINKS_TO edges from-and-to these nodes). Use the same routine `ki rm` uses (see *Removal routine* below).
3. Re-ingest from disk as if it were a fresh ingest, *but* keep the existing `Vault.uri` from the marker (no slug reassignment).
4. The user-authored `description:` in the marker is preserved across the cycle.

**Why nuke-before-reindex instead of incremental diff:** keeps the per-document path simple. No `fileHash`-driven stale-doc cleanup, no orphan-section detection, no resolver invalidation. The downside is that re-indexing a million-doc vault re-processes every file. That's acceptable at v0.4.0 envelopes (≤ 10k docs / 1 GB) and is easy to upgrade later if a real perf complaint shows up. Track [#3](https://github.com/zach-blumenfeld/knowledge-index/issues/3) — closed by this PR — for the history.

**Flags:**

- `--chunk-size N` — rows per batched `DETACH DELETE` transaction during the pre-ingest nuke step (default 1000). Raise it on small graphs to cut transaction overhead; lower it if the JVM heap is tight. (See *Batched DETACH DELETE* below for why this exists at all.)

## `ki rm <vault>`

**Vault-only.** The only valid argument shape is a path that resolves to a vault root (or a Vault.uri slug). Passing a document path, a subdirectory, or a non-vault path errors with this message:

> `ki rm` only operates on vaults. Individual folders, documents, and sections sync at the vault level to mirror what's currently on disk. Use `ki index <vault>` to refresh, or `ki rm <vault>` to remove the whole vault.

**Behavior:**

1. Resolve target to a `Vault.uri` (path → read marker → take `uri:`; slug-form → use literally).
2. Run `ki rm`'s pre-removal preview (count of Documents, Sections; surfaced with the vault `displayName`).
3. Typed-confirmation prompt (`type the vault display-name to confirm`), unless `--yes`.
4. Run the removal routine (see below).
5. Drop `.ki/vault.yaml` from disk, unless `--keep-marker` is passed.

**Flags:**

- `--yes` — skip the typed-confirmation prompt (intended for scripts / agent auto-mode).
- `--keep-marker` — leave `.ki/vault.yaml` on disk so a subsequent `ki index` rebuilds onto the same `Vault.uri` (the canonical "reset this vault" idiom).
- `--dry-run` — show the counts; don't run the removal.
- `--chunk-size N` — same as `ki index`.

## `ki nuke`

Reset the entire graph and remove every `.ki/vault.yaml` ki knows about.

**Behavior:**

1. Gather all `:Vault` nodes (URIs + paths) before any removal — needed for marker cleanup later.
2. Typed-confirmation prompt (`type 'nuke' to confirm` or similar), unless `--yes`.
3. Run the removal routine on every vault.
4. **Drop all indexes and constraints** that `ki` owns. The next `ki index` will recreate them via `ensure_schema`. Done because schema-changing migrations leave behind orphaned indexes that cause subtle bugs later; a full nuke is the right moment to reset that state.
5. Remove `.ki/vault.yaml` from every vault root the graph knew about, unless `--keep-marker` is passed.

**Flags:**

- `--yes`, `--keep-marker`, `--chunk-size N` — same semantics as `ki rm`.

`ki nuke` is intentionally not exposed via auto-mode without explicit user consent (touches every vault, drops schema). See the agent auto-mode rules in `docs/requirements_v01_mvp.md`.

## Removal routine

The shared procedure used by `ki index` (pre-ingest), `ki rm`, and `ki nuke` (per-vault).

Given a `Vault.uri`:

1. **Collect outbound external LINKS_TO targets.** Before removing anything, snapshot the URIs of nodes outside this vault that the vault's content links to:
   ```cypher
   MATCH (src)-[:LINKS_TO]->(tgt)
   WHERE (src.uri = $vaultUri OR src.uri STARTS WITH $vaultUri + '/')
     AND NOT (tgt.uri = $vaultUri OR tgt.uri STARTS WITH $vaultUri + '/')
   RETURN DISTINCT tgt.uri AS uri
   ```
   These are the only external nodes whose degree could plausibly drop to zero as a result of the removal.

2. **Remove the vault subtree.** Batched DETACH DELETE on everything with URI matching this vault:
   ```cypher
   MATCH (n) WHERE n.uri = $vaultUri OR n.uri STARTS WITH $vaultUri + '/'
   CALL (n) {
     DETACH DELETE n
   } IN TRANSACTIONS OF $chunkSize ROWS
   ```
   This removes the `:Vault` node, every `:Folder` / `:Document` / `:Section` under it, all `:HAS` edges in the subtree, and all `:LINKS_TO` edges that touch this subtree (incoming and outgoing).

3. **Orphan GC on the collected targets only.** For each URI from step 1, check its post-step-2 degree and remove if zero. Scoped to the snapshot list so we never race with a concurrent ingest that's mid-write (a freshly-created Document with no edges yet would be a false positive for a global sweep, but it's not in our snapshot):
   ```cypher
   UNWIND $candidateUris AS u
   MATCH (n {uri: u})
   WHERE NOT (n)--()
   CALL (n) {
     DETACH DELETE n
   } IN TRANSACTIONS OF $chunkSize ROWS
   ```
   In 0.4.0 this rarely fires — WIKILINK_UNRESOLVED Documents live inside their source vault (HAS-attached to it) and are removed by step 2, and real Documents in *other* vaults stay alive via their own HAS edge to their own Vault. The query is still load-bearing for #37's external URL_LINK Documents and for any future ingest path that can leave external orphans.

The single edge case the design needs to cover is **outbound LINKS_TO from the removed vault to external node A: remove A iff its post-step-2 degree is zero, else keep**. Steps 1 and 3 together implement that rule. Inbound LINKS_TO to the removed vault is not a concern in 0.4.0 — the per-vault wikilink resolver doesn't produce cross-vault links, and any such edge would be dropped by `DETACH DELETE` in step 2 anyway as a side-effect of removing its target.

**Why not a global "degree-zero sweep" on all Folder/Document/Section nodes after step 2?** It's tempting (one less query, no Python-side state) but **unsafe under concurrent writes**: the ingest pipeline writes nodes and their HAS edges in separate batched transactions, so a freshly-MERGEd Document briefly has no edges between the doc-write batch and the HAS-edge batch. A global sweep running on a different connection could see that node as orphaned and remove it. The scoped snapshot-and-recheck approach avoids the race entirely by only touching nodes the current operation knows it affected.

## Batched DETACH DELETE

A naive `MATCH (n) WHERE ... DETACH DELETE n` runs in a single transaction. On large vaults this trips the Neo4j JVM heap and aborts with an OOM error mid-removal, leaving the graph in a half-removed state.

The fix is `CALL { ... } IN TRANSACTIONS OF $n ROWS`: each chunk commits before the next starts, so heap usage stays bounded.

```cypher
MATCH (n) WHERE n.uri = $vaultUri OR n.uri STARTS WITH $vaultUri + '/'
CALL (n) {
  DETACH DELETE n
} IN TRANSACTIONS OF $chunkSize ROWS
```

**Default chunk size: 1000.** Tuned empirically — small enough to fit a typical batch in heap on a 1 GB Neo4j, large enough that transaction overhead doesn't dominate.

**`--chunk-size` flag.** Exposed on `ki index`, `ki rm`, and `ki nuke`. Help text reads roughly: *"Rows per batched-remove transaction. If you hit Neo4j OOM during removal, lower this (e.g. 200); on small graphs where you want fewer transactions, raise it (e.g. 5000)."* We do not currently catch OOM programmatically — the agent reads the flag help and adjusts.

## Why not partial / incremental sync

We considered keeping `ki rm` document-level and adding stale-doc cleanup to `ki index`. Rejected because:

- **State drift.** Three "remove" code paths (file deletion + index, manual `ki rm <doc>`, stale-doc sweep during re-index) means three behaviors to keep in sync with each other and with the graph.
- **Resolver invalidation.** Partial removal needs to invalidate the wikilink resolver entries the removed docs participated in. Easy to forget.
- **No real demand yet.** Pre-1.0; nobody is hitting "I removed one file from a 10k-doc vault and don't want to re-index the whole thing." If that demand shows up, we add a partial-sync mode then with eyes-open design rather than hedging now.

Vault-level sync is the floor — we can always add document-level sync later. The reverse is harder because users would write workflows assuming the partial-sync semantics.
