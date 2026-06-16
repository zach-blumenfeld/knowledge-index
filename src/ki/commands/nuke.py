"""`ki nuke` — reset the entire graph and remove every `.ki/vault.yaml` ki knows about.

See `docs/data-model/index_rm_behavior.md` *ki nuke* for the full spec. Behavior:
  1. Snapshot every Vault's (uri, path) from the graph.
  2. Typed-confirmation prompt (unless --yes).
  3. Batched DETACH DELETE every node.
  4. Drop all ki-owned constraints and the fulltext index.
  5. Remove .ki/vault.yaml from every snapshotted vault root (unless --keep-marker).

Not exposed via auto-mode without explicit user consent — touches every
vault, drops schema. Run only when the user has typed "nuke".
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.prompt import Prompt

from ..config import find_config_path, load_config
from ..ingest.remove import (
    DEFAULT_CHUNK_SIZE,
    list_all_vaults,
    remove_all_nodes_and_schema,
)
from ..neo4j_client import driver_for
from ..profile_resolve import resolve_profile
from ..vault import remove_vault_marker, vault_marker_path

console = Console()


def cmd_nuke(
    *,
    profile: str | None,
    dry_run: bool,
    yes: bool,
    keep_marker: bool,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> int:
    cfg_path = find_config_path()
    if cfg_path is None:
        raise click.ClickException("no ki config found — run `ki configure` first")
    cfg = load_config(cfg_path)
    prof = resolve_profile(cfg, profile)

    with driver_for(prof) as driver, driver.session() as session:
        vaults = list_all_vaults(session)

        if not vaults and dry_run:
            console.print("[dim]No vaults indexed — nothing to remove.[/dim]")
            return 0

        console.print(
            f"This will [bold red]reset the entire ki graph[/bold red] "
            f"({len(vaults)} vault(s)) and drop all indexes/constraints.\n"
            f"  source files: [green]untouched[/green]"
        )
        for v in vaults:
            console.print(f"  - {v['uri']}  [dim]({v['path']})[/dim]")

        if dry_run:
            console.print("[yellow]dry-run[/yellow] no changes made.")
            return 0
        if not yes:
            typed = Prompt.ask(
                "Type [yellow]nuke[/yellow] to confirm"
            )
            if typed.strip() != "nuke":
                console.print("[yellow]Confirmation mismatch — cancelled.[/yellow]")
                return 1

        remove_all_nodes_and_schema(session, chunk_size=chunk_size)

    # Marker cleanup runs after the graph wipe so a partial failure during the
    # graph step doesn't leave us with marker-less but populated vaults.
    if not keep_marker:
        for v in vaults:
            path = v.get("path")
            if not path:
                continue
            try:
                remove_vault_marker(Path(path))
            except FileNotFoundError:
                # Path no longer on disk (vault moved / removed). Marker
                # cleanup is best-effort; the graph wipe is the load-bearing
                # operation.
                continue
            console.print(
                f"[green]✓[/green] removed marker at "
                f"{vault_marker_path(Path(path))}"
            )
    else:
        console.print(
            "[dim]Markers preserved — next `ki index` rebuilds each vault "
            "under the same uri.[/dim]"
        )

    console.print(
        f"[green]✓[/green] nuked {len(vaults)} vault(s); graph + schema reset"
    )
    return 0
