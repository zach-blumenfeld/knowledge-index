"""`ki search <query>` — fulltext retrieval.

v1 flags:
  --type {section|document|vault}  which retrieval shape to use
                                   (B.2, B.1, B.11 respectively)
  --k N                            result limit
  --json                           emit machine-readable JSON

Backlinks / neighbour-style traversal (formerly `--type neighbors`) is
removed in 0.4.0; see #35 for the planned replacement.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from ..config import find_config_path, load_config
from ..neo4j_client import driver_for
from ..search.queries import run_b1, run_b2, run_vault_search

console = Console()


def _warn_missing_vault_description(rows: list[dict[str, Any]]) -> None:
    """One-line stderr warning per vault row whose description is null/empty.

    Hint that the user (or routing agent) should set `description:` in
    `.ki/vault.yaml` so future vault searches are more discriminative.
    """
    for r in rows:
        desc = r.get("description")
        if desc is None or (isinstance(desc, str) and not desc.strip()):
            name = r.get("display_name") or r.get("name") or r.get("vault_uri")
            print(
                f"warning: vault {name!r} has no description set — "
                "add one to .ki/vault.yaml for sharper agent routing.",
                file=sys.stderr,
            )


def cmd_search(
    query: str,
    *,
    profile: str | None,
    search_type: str,
    k: int,
    as_json: bool,
) -> int:
    cfg_path = find_config_path()
    if cfg_path is None:
        raise click.ClickException("no ki config found — run `ki configure` first")
    cfg = load_config(cfg_path)
    prof = cfg.get_profile(profile)

    with driver_for(prof) as driver, driver.session() as session:
        if search_type == "document":
            rows = run_b1(session, query, k=k)
        elif search_type == "section":
            rows = run_b2(session, query, k=k)
        elif search_type == "vault":
            rows = run_vault_search(session, query, k=k)
        else:
            raise click.ClickException(f"unknown --type {search_type}")

    if as_json:
        click.echo(json.dumps(rows, default=str, indent=2))
    else:
        _render_table(rows, search_type)
    if search_type == "vault":
        _warn_missing_vault_description(rows)
    return 0


def _render_table(rows: list[dict[str, Any]], search_type: str) -> None:
    if not rows:
        console.print("[dim](no results)[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    if search_type == "document":
        table.add_column("score", style="green", justify="right")
        table.add_column("title")
        table.add_column("uri", style="dim")
        for r in rows:
            table.add_row(f"{r.get('score', 0):.2f}", str(r.get("title")), str(r.get("document_uri")))
    elif search_type == "section":
        table.add_column("score", style="green", justify="right")
        table.add_column("heading")
        table.add_column("uri", style="dim")
        for r in rows:
            table.add_row(
                f"{r.get('score', 0):.2f}",
                f"{'#' * (r.get('heading_level') or 1)} {r.get('heading')}",
                str(r.get("section_uri")),
            )
    elif search_type == "vault":
        table.add_column("score", style="green", justify="right")
        table.add_column("name")
        table.add_column("uri", style="dim")
        table.add_column("path", style="dim")
        table.add_column("description")
        for r in rows:
            desc = r.get("description") or ""
            if len(desc) > 120:
                desc = desc[:117].rstrip() + "..."
            table.add_row(
                f"{r.get('score', 0):.2f}",
                str(r.get("display_name") or r.get("name")),
                str(r.get("vault_uri")),
                str(r.get("path") or ""),
                desc,
            )
    console.print(table)
