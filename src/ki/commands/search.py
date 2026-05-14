"""`ki search <query>` — fulltext + graph retrieval.

v1 flags:
  --type {section|document|neighbors}    which retrieval shape to use
                                         (B.2, B.1, B.3 respectively)
  --k N                                  result limit / depth
  --json                                 emit machine-readable JSON
  --doc-uri URI                          (--type neighbors) start document
"""

from __future__ import annotations

import json
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from ..config import find_config_path, load_config
from ..neo4j_client import driver_for
from ..search.queries import run_b1, run_b2, run_b3

console = Console()


def cmd_search(
    query: str,
    *,
    profile: str | None,
    search_type: str,
    k: int,
    as_json: bool,
    doc_uri: str | None,
) -> int:
    cfg_path = find_config_path()
    if cfg_path is None:
        raise click.ClickException("no ki config found — run `ki configure` first")
    cfg = load_config(cfg_path)
    prof = cfg.get_profile(profile)

    with driver_for(prof) as driver:
        with driver.session() as session:
            if search_type == "document":
                rows = run_b1(session, query, k=k)
            elif search_type == "section":
                rows = run_b2(session, query, k=k)
            elif search_type == "neighbors":
                if not doc_uri:
                    raise click.ClickException(
                        "--type neighbors requires --doc-uri <uri>"
                    )
                rows = run_b3(session, doc_uri, n=k)
            else:
                raise click.ClickException(f"unknown --type {search_type}")

    if as_json:
        click.echo(json.dumps(rows, default=str, indent=2))
        return 0
    _render_table(rows, search_type)
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
        table.add_column("document")
        for r in rows:
            table.add_row(
                f"{r.get('score', 0):.2f}",
                f"{'#' * (r.get('heading_level') or 1)} {r.get('heading')}",
                str(r.get("document_title")),
            )
    elif search_type == "neighbors":
        table.add_column("distance", style="green", justify="right")
        table.add_column("title")
        table.add_column("uri", style="dim")
        for r in rows:
            table.add_row(str(r.get("distance")), str(r.get("title")), str(r.get("document_uri")))
    console.print(table)
