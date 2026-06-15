"""`ki status` — the layered vault state machine.

Resolves a directory to exactly one state, reporting the FIRST blocking one.
Layers, each needing the one above it to pass (see SKILL.md *Step 2*):

  1. disk marker      → is there a `.ki/vault.yaml` at or above here?
  2. profile binding  → does the bound profile exist in config?
  3. Neo4j reachable  → can we connect to that profile? (down/unresponsive/auth)
  4. graph state      → indexed? in sync with disk?

The state strings here are the public contract — `ki status --json` emits them
verbatim and the SKILL routes on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import click

from .config import Config
from .ingest import queries as Q
from .ingest.pipeline import DEFAULT_MAX_FILE_SIZE
from .neo4j_client import (
    CONN_REACHABLE,
    classify_connectivity,
    driver_for,
)
from .parser.markdown import hash_bytes
from .profile_resolve import resolve_profile
from .vault import (
    document_uri,
    find_vault_root,
    iter_markdown_files,
    read_vault_profile,
    read_vault_uri,
)

# --- states (public contract) ------------------------------------------------
NOT_A_VAULT = "NOT_A_VAULT"
PROFILE_MISSING = "PROFILE_MISSING"
NEO4J_DOWN = "NEO4J_DOWN"
NEO4J_UNRESPONSIVE = "NEO4J_UNRESPONSIVE"
AUTH_ERROR = "AUTH_ERROR"
NOT_INDEXED = "NOT_INDEXED"
STALE = "STALE"
READY = "READY"


@dataclass
class StatusResult:
    state: str
    path: Path
    vault_root: Path | None = None
    vault_uri: str | None = None
    profile: str | None = None
    detail: dict = field(default_factory=dict)
    message: str = ""


def _disk_md_docs(
    vault_root: Path, vault_uri: str, max_file_size: int = DEFAULT_MAX_FILE_SIZE
) -> dict[str, Path]:
    """Map {document_uri: path} for the `.md` files `ki index` would ingest.

    Mirrors the indexer exactly: `iter_markdown_files` (same `*.md` glob + same
    ignore-dir rules) then the same oversize filter, so STALE never flags a file
    the indexer would skip.
    """
    out: dict[str, Path] = {}
    for p in iter_markdown_files(vault_root):
        try:
            if p.stat().st_size > max_file_size:
                continue
        except OSError:
            continue
        rel = p.relative_to(vault_root)
        out[document_uri(vault_uri, rel)] = p
    return out


def graph_state(session, vault_root: Path, vault_uri: str) -> tuple[str, dict]:
    """Decide NOT_INDEXED / STALE / READY for a reachable, indexed-or-not vault.

    Two-tier STALE: first compare the *set* of primary-doc URIs (disk vs graph)
    — any add/remove is STALE without reading a byte. Only if the sets match do
    we read + hash each file to catch in-place edits (no parsing).
    """
    n = session.run(Q.VAULT_EXISTS, vaultUri=vault_uri).single()["n"]
    if not n:
        return NOT_INDEXED, {}

    disk = _disk_md_docs(vault_root, vault_uri)
    rows = session.run(Q.LIST_LOCAL_FILE_DOC_HASHES, prefix=vault_uri + "/")
    graph = {r["uri"]: r["fileHash"] for r in rows}

    disk_uris, graph_uris = set(disk), set(graph)
    added = disk_uris - graph_uris      # on disk, never indexed
    removed = graph_uris - disk_uris    # indexed, gone from disk
    if added or removed:
        # Detail carries both counts (for the headline) and the uri lists (for
        # `ki status -v` / --json). Sorted for stable output.
        return STALE, {
            "added": len(added), "removed": len(removed), "changed": 0,
            "added_uris": sorted(added), "removed_uris": sorted(removed),
            "changed_uris": [],
        }

    changed_uris: list[str] = []
    for uri, path in disk.items():
        try:
            if graph.get(uri) != hash_bytes(path.read_bytes()):
                changed_uris.append(uri)
        except OSError:
            changed_uris.append(uri)  # vanished mid-check → treat as a change
    if changed_uris:
        return STALE, {
            "added": 0, "removed": 0, "changed": len(changed_uris),
            "added_uris": [], "removed_uris": [], "changed_uris": sorted(changed_uris),
        }

    return READY, {}


def compute_status(
    cfg: Config,
    start_dir: Path,
    *,
    profile_flag: str | None = None,
    conn_timeout: float = 5.0,
) -> StatusResult:
    """Walk the layers and return the first blocking state."""
    start = Path(start_dir).expanduser().resolve()

    # Layer 1 — disk marker.
    root = find_vault_root(start)
    if root is None:
        return StatusResult(state=NOT_A_VAULT, path=start)

    vault_uri = read_vault_uri(root)

    # Layer 2 — profile binding. resolve_profile raises ClickException
    # (incl. BoundProfileMissing) for any unresolvable profile; status turns
    # that into a state instead of aborting.
    try:
        prof = resolve_profile(cfg, profile_flag, start_dir=root)
    except click.ClickException as exc:
        return StatusResult(
            state=PROFILE_MISSING, path=start, vault_root=root,
            vault_uri=vault_uri, profile=read_vault_profile(root),
            message=exc.format_message(),
        )

    # Layer 3 — Neo4j reachability. CONN_* failure values ARE the state strings.
    conn = classify_connectivity(prof, conn_timeout)
    if conn != CONN_REACHABLE:
        return StatusResult(
            state=conn, path=start, vault_root=root,
            vault_uri=vault_uri, profile=prof.name,
        )

    # Layer 4 — graph state.
    with driver_for(prof) as driver, driver.session() as session:
        state, detail = graph_state(session, root, vault_uri)
    return StatusResult(
        state=state, path=start, vault_root=root,
        vault_uri=vault_uri, profile=prof.name, detail=detail,
    )
