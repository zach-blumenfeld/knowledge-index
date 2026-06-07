"""`ki profile <verb>` — connection-profile management.

v0.4.0 ships one verb: `ki profile list`, which prints every profile in
`config.yaml`. Reads config only — **no Neo4j connection** — so it works even
when every backend is down (that's why the SKILL routes here when picking a
profile to bind a fresh vault).
"""

from __future__ import annotations

import json

import click
from rich.console import Console
from rich.table import Table

from ..config import find_config_path, load_config

console = Console()


def cmd_profile_list(as_json: bool = False) -> int:
    cfg_path = find_config_path()
    cfg = load_config(cfg_path)

    rows = []
    for name, p in cfg.profiles.items():
        rows.append({
            "name": name,
            "default": name == cfg.default_profile,
            "uri": p.uri,
            "source": p.source,
            "database": p.database,  # None → server's home database
        })

    if as_json:
        click.echo(json.dumps(rows, default=str, indent=2))
    else:
        _render_table(rows, cfg_path)
    return 0


def _render_table(rows: list[dict], cfg_path) -> None:
    if not rows:
        console.print(
            "[dim](no profiles configured)[/dim] — run "
            "[cyan]ki configure[/cyan] to add one."
        )
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("")  # default marker
    table.add_column("name")
    table.add_column("uri", style="dim")
    table.add_column("source")
    table.add_column("database")
    for r in rows:
        table.add_row(
            "[green]*[/green]" if r["default"] else "",
            r["name"],
            r["uri"],
            r["source"],
            r["database"] or "[dim]home[/dim]",
        )
    console.print(table)
    if cfg_path:
        console.print(f"[dim]{cfg_path}  (* = default)[/dim]")
