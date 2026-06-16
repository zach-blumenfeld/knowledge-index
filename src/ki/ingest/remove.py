"""Shared vault-removal routines for `ki drop`, `ki index` (pre-ingest nuke),
and `ki nuke`.

The behavior here is the contract that `docs/data-model/index_rm_behavior.md` describes —
read that doc first if you're touching anything in this module. We say
**remove** (not delete) in user-facing strings and code comments; Cypher
keywords (`DELETE`, `DETACH DELETE`) stay verbatim as language tokens.

The three-step removal routine `remove_vault` runs:
  1. Snapshot outbound external LINKS_TO targets (URIs only).
  2. Batched DETACH DELETE of the vault subtree.
  3. Orphan GC on the snapshot — recheck degree, remove if zero.

Steps 2 and 3 substitute the chunk-size literal client-side because
`CALL ... IN TRANSACTIONS OF n ROWS` rejects Cypher parameters in `n`
(same constraint as B.3 / B.12 quantified-path-pattern quantifiers).
"""

from __future__ import annotations

import logging

from . import queries as Q

log = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 1000


def _coerce_chunk_size(chunk_size: int) -> int:
    """Clamp to a sensible range and ensure int — defensive given client-side substitution."""
    return max(1, int(chunk_size))


def _substitute_chunk_size(query: str, chunk_size: int) -> str:
    return query.replace("$chunkSize", str(_coerce_chunk_size(chunk_size)))


def remove_vault(
    session,
    vault_uri: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> dict[str, int]:
    """Remove a vault and all its content, GC'ing newly-orphaned external targets.

    Returns a `{orphans_collected, orphans_removed}` dict for telemetry.
    """
    # Step 1 — snapshot external LINKS_TO targets before we touch anything.
    candidate_rows = list(
        session.run(Q.COLLECT_EXTERNAL_LINKS_TARGETS, vaultUri=vault_uri)
    )
    candidate_uris = [r["uri"] for r in candidate_rows]

    # Step 2 — batched DETACH DELETE of the vault subtree. CALL IN TRANSACTIONS
    # cannot run inside `execute_write` (managed tx) — use `session.run` so
    # the driver opens an implicit transaction for this single statement.
    session.run(
        _substitute_chunk_size(Q.REMOVE_VAULT_SUBTREE_BATCHED, chunk_size),
        vaultUri=vault_uri,
    ).consume()

    # Step 3 — orphan GC on the snapshot only (skip if nothing to check).
    orphans_removed = 0
    if candidate_uris:
        # We can't easily distinguish "removed" from "still has edges, skipped"
        # in a single batched call; count by re-querying which ones survived.
        result = session.run(
            _substitute_chunk_size(Q.REMOVE_ORPHAN_TARGETS_BATCHED, chunk_size),
            candidateUris=candidate_uris,
        )
        result.consume()
        survivors = list(
            session.run(
                "UNWIND $uris AS u MATCH (n {uri: u}) RETURN count(n) AS n",
                uris=candidate_uris,
            )
        )
        survivor_count = survivors[0]["n"] if survivors else 0
        orphans_removed = max(0, len(candidate_uris) - survivor_count)

    return {
        "orphans_collected": len(candidate_uris),
        "orphans_removed": orphans_removed,
    }


def count_subtree(session, root_uri: str) -> dict[str, int]:
    """`{label: count}` for the subtree rooted at `root_uri` (3-part containment).

    Used by `ki rm --dry-run` to preview what removal would touch without
    deleting. Empty dict means nothing is indexed at that uri.
    """
    rows = session.run(Q.COUNT_SUBTREE_BY_LABEL, root=root_uri)
    return {r["label"]: r["n"] for r in rows}


def remove_subtree(
    session,
    root_uri: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> dict[str, int]:
    """Remove a document/folder subtree (root + descendants), GC'ing orphaned externals.

    The subtree is the 3-part containment scope (`root`, its `/`-descendants,
    and a document's `#`-sections) — the same scope `ki search --under` uses.
    The caller (`ki rm`) must guard that `root_uri` is a Document or Folder:
    a Vault belongs to `remove_vault`, and a bare Section isn't a removable
    on-disk object.

    Same three-step routine as `remove_vault` (snapshot externals → batched
    DETACH DELETE → orphan GC). Returns
    `{nodes_removed, orphans_collected, orphans_removed}`.
    """
    # Step 0 — count the subtree before we delete it (for the report).
    nodes_removed = sum(count_subtree(session, root_uri).values())

    # Step 1 — snapshot external LINKS_TO targets reachable from the subtree.
    candidate_uris = [
        r["uri"]
        for r in session.run(Q.COLLECT_EXTERNAL_LINKS_TARGETS_SUBTREE, root=root_uri)
    ]

    # Step 2 — batched DETACH DELETE of the subtree (implicit tx; see remove_vault).
    session.run(
        _substitute_chunk_size(Q.REMOVE_SUBTREE_BATCHED, chunk_size),
        root=root_uri,
    ).consume()

    # Step 3 — orphan GC on the snapshot only (skip if nothing to check).
    orphans_removed = 0
    if candidate_uris:
        session.run(
            _substitute_chunk_size(Q.REMOVE_ORPHAN_TARGETS_BATCHED, chunk_size),
            candidateUris=candidate_uris,
        ).consume()
        survivors = list(
            session.run(
                "UNWIND $uris AS u MATCH (n {uri: u}) RETURN count(n) AS n",
                uris=candidate_uris,
            )
        )
        survivor_count = survivors[0]["n"] if survivors else 0
        orphans_removed = max(0, len(candidate_uris) - survivor_count)

    return {
        "nodes_removed": nodes_removed,
        "orphans_collected": len(candidate_uris),
        "orphans_removed": orphans_removed,
    }


def remove_all_nodes_and_schema(
    session,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> None:
    """`ki nuke`'s graph-side: batched DETACH DELETE of every node, then drop schema.

    Schema drop is idempotent (every statement is `IF EXISTS`-guarded) — a
    second invocation on an empty graph is a no-op. The next `ki index`
    recreates the schema via `ensure_schema`.
    """
    session.run(
        _substitute_chunk_size(Q.REMOVE_ALL_NODES_BATCHED, chunk_size),
    ).consume()
    for stmt in Q.DROP_SCHEMA_STATEMENTS:
        session.run(stmt).consume()


def list_all_vaults(session) -> list[dict]:
    """Return `[{uri, path}, ...]` for every Vault currently in the graph.

    Used by `ki nuke` to enumerate marker-file paths so they can be removed
    from disk after the graph wipe.
    """
    rows = session.run(Q.LIST_ALL_VAULTS)
    return [dict(r) for r in rows]
