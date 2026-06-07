"""`ki status [path]` — report the vault's state and the next action.

Thin IO wrapper over `ki.status.compute_status`. Renders the first blocking
state with a concrete next step (human), or the raw state machine (`--json`).
Exit code is 0 only for READY, 1 otherwise — so `ki status && ki search …`
composes.
"""

from __future__ import annotations

import json as jsonlib
import sys
from pathlib import Path

from rich.console import Console

from ..config import find_config_path, load_config
from ..status import (
    AUTH_ERROR,
    NEO4J_DOWN,
    NEO4J_UNRESPONSIVE,
    NOT_A_VAULT,
    NOT_INDEXED,
    PROFILE_MISSING,
    READY,
    STALE,
    StatusResult,
    compute_status,
)

console = Console()

# (symbol/colour, one-line meaning). Action text is built per-state below
# because some states fold in dynamic detail (file counts) or the raw error.
_HEADLINE = {
    NOT_A_VAULT: ("[yellow]○[/yellow]", "not a vault yet"),
    PROFILE_MISSING: ("[red]✗[/red]", "bound profile not in config"),
    NEO4J_DOWN: ("[red]✗[/red]", "Neo4j isn't running"),
    NEO4J_UNRESPONSIVE: ("[yellow]…[/yellow]", "Neo4j up but not answering"),
    AUTH_ERROR: ("[red]✗[/red]", "wrong Neo4j credentials"),
    NOT_INDEXED: ("[yellow]○[/yellow]", "vault not indexed yet"),
    STALE: ("[yellow]●[/yellow]", "index out of sync with disk"),
    READY: ("[green]✓[/green]", "indexed and in sync"),
}


def _action(r: StatusResult) -> str:
    if r.state == NOT_A_VAULT:
        return (
            "Pick a profile (`ki profile list`) and run "
            "`ki index . --profile <p> --description \"...\"`. "
            "No profiles yet → references/configure-profile.md."
        )
    if r.state == PROFILE_MISSING:
        return (
            "Re-bind with `ki use <profile>`, or re-create it with "
            "`ki configure`. (references/configure-profile.md)"
        )
    if r.state == NEO4J_DOWN:
        return "Start Neo4j → references/neo4j-troubleshoot.md."
    if r.state == NEO4J_UNRESPONSIVE:
        return "Wait for it to finish starting, then references/neo4j-troubleshoot.md."
    if r.state == AUTH_ERROR:
        return (
            "Re-enter credentials with `ki configure` "
            "(references/configure-profile.md) — not a restart."
        )
    if r.state == NOT_INDEXED:
        return "Run `ki index .` to index this vault."
    if r.state == STALE:
        d = r.detail
        bits = []
        if d.get("added"):
            bits.append(f"{d['added']} added")
        if d.get("removed"):
            bits.append(f"{d['removed']} removed")
        if d.get("changed"):
            bits.append(f"{d['changed']} changed")
        what = ", ".join(bits) if bits else "files changed"
        return f"{what} since last index → run `ki index .` to refresh."
    if r.state == READY:
        return "Use it: `ki outline <vault uri>`, then `ki search` / `ki get`."
    return ""


def _render(r: StatusResult) -> None:
    symbol, meaning = _HEADLINE.get(r.state, ("[red]✗[/red]", ""))
    console.print(f"{symbol} [bold]{r.state}[/bold] — {meaning}")
    if r.vault_uri:
        prof = f"  (profile: {r.profile})" if r.profile else ""
        console.print(f"  vault: [cyan]{r.vault_uri}[/cyan]{prof}")
    if r.vault_root:
        console.print(f"  path:  {r.vault_root}")
    if r.message:
        console.print(f"  [dim]{r.message}[/dim]")
    console.print(f"  → {_action(r)}")


def cmd_status(
    path: Path | None,
    *,
    profile: str | None,
    as_json: bool,
    conn_timeout: float = 5.0,
) -> int:
    cfg_path = find_config_path()
    # No config at all is a legitimate first-run state, not a crash — status
    # still reports NOT_A_VAULT (disk layer needs no Neo4j) when relevant.
    cfg = load_config(cfg_path)

    start = Path(path).expanduser().resolve() if path else Path.cwd()
    result = compute_status(
        cfg, start, profile_flag=profile, conn_timeout=conn_timeout
    )

    if as_json:
        payload = {
            "state": result.state,
            "vault_uri": result.vault_uri,
            "profile": result.profile,
            "path": str(result.vault_root) if result.vault_root else str(start),
            "detail": result.detail,
            "message": result.message,
        }
        sys.stdout.write(jsonlib.dumps(payload, indent=2) + "\n")
    else:
        _render(result)

    return 0 if result.state == READY else 1
