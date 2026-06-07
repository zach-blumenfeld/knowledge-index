"""`ki vault <verb>` — vault-management commands.

v0.4.0 ships one verb: `ki vault list`, which prints every indexed vault with
its user-authored `description` (sourced from each vault's `.ki/vault.yaml`).
"""

from __future__ import annotations

import json
import sys
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from ..config import find_config_path, load_config
from ..ingest.queries import VAULT_LIST
from ..neo4j_client import driver_for
from ..profile_resolve import resolve_profile

console = Console()


def cmd_vault_list(profile: str | None, as_json: bool = False) -> int:
    cfg_path = find_config_path()
    if cfg_path is None:
        raise click.ClickException("no ki config found — run `ki configure` first")
    cfg = load_config(cfg_path)
    prof = resolve_profile(cfg, profile)

    with driver_for(prof) as driver:
        with driver.session() as session:
            rows = [dict(r) for r in session.run(VAULT_LIST)]

    if as_json:
        click.echo(json.dumps(rows, default=str, indent=2))
    else:
        _render_table(rows)
    _warn_missing_description(rows)
    return 0


def _render_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        console.print("[dim](no indexed vaults)[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("name")
    table.add_column("path", style="dim")
    table.add_column("description")
    for r in rows:
        desc = r.get("description") or ""
        if len(desc) > 120:
            desc = desc[:117].rstrip() + "..."
        table.add_row(
            str(r.get("displayName") or r.get("name") or ""),
            str(r.get("path") or ""),
            desc,
        )
    console.print(table)


def _warn_missing_description(rows: list[dict[str, Any]]) -> None:
    for r in rows:
        desc = r.get("description")
        if desc is None or (isinstance(desc, str) and not desc.strip()):
            name = r.get("displayName") or r.get("name") or r.get("uri")
            print(
                f"warning: vault {name!r} has no description set — "
                "add one to .ki/vault.yaml for sharper agent routing.",
                file=sys.stderr,
            )
