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

from .. import neo4j_cli
from ..config import find_config_path, load_config

console = Console()


def cmd_profile_sync(name: str | None = None) -> int:
    """Register ki profiles into neo4j-cli's credential store.

    So agents can run `neo4j-cli query "<cypher>" --credential <name>` for
    graph-reasoning without handling the password (ki holds the secret; the
    agent uses only the name). Syncs all profiles, or just `name` if given.
    """
    if not neo4j_cli.is_available():
        raise click.ClickException(
            "neo4j-cli is not installed (needed for graph-reasoning delegation). "
            "Install it from https://github.com/neo4j-labs/neo4j-cli, then re-run."
        )
    cfg_path = find_config_path()
    cfg = load_config(cfg_path)
    if name:
        try:
            targets = [cfg.get_profile(name)]
        except KeyError as exc:
            raise click.ClickException(str(exc)) from exc
    else:
        targets = list(cfg.profiles.values())
    if not targets:
        console.print("[dim](no profiles to sync — run `ki configure` first)[/dim]")
        return 0
    for prof in targets:
        try:
            neo4j_cli.register_credential(prof)
        except Exception as exc:  # noqa: BLE001
            raise click.ClickException(
                f"failed to register profile {prof.name!r} with neo4j-cli: {exc}"
            ) from exc
        console.print(f"[green]✓[/green] {prof.name}")
    console.print(
        "[dim]Agents can now run "
        '`neo4j-cli query "<cypher>" --credential <name>`.[/dim]'
    )
    return 0


def cmd_profile_list(as_json: bool = False) -> int:
    cfg_path = find_config_path()
    cfg = load_config(cfg_path)

    rows = []
    for name, p in cfg.profiles.items():
        rows.append({
            "name": name,
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
    table.add_column("name")
    table.add_column("uri", style="dim")
    table.add_column("source")
    table.add_column("database")
    for r in rows:
        table.add_row(
            r["name"],
            r["uri"],
            r["source"],
            r["database"] or "[dim]home[/dim]",
        )
    console.print(table)
    if cfg_path:
        console.print(f"[dim]{cfg_path}[/dim]")
