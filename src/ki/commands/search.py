"""`ki search <query>` — fulltext retrieval over documents and sections.

One sweep over the shared `content_search` index (which covers
displayName + content + aliases + description at once). By default it searches
the vault you're standing in; widen with `--all` (whole profile) or target
another with `--vault <uri>`.

Flags:
  --types <csv>   Subset of {document,section} (default: both).
  --vault <uri>   Scope to a specific vault (default: the active/cwd vault).
  --all           Search across every vault in the profile.
  --k N           Result cap (default: 10).
  --json          Emit machine-readable JSON rows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
from neo4j.exceptions import ClientError
from rich.console import Console
from rich.table import Table

from ..config import find_config_path, load_config
from ..neo4j_client import driver_for
from ..profile_resolve import resolve_profile
from ..search.queries import run_search
from ..vault import find_vault_root, read_vault_uri

console = Console()

VALID_TYPES = ("document", "section")
DEFAULT_TYPES = "document,section"
TYPE_LETTER = {"Document": "D", "Section": "S"}


def _parse_types(types_csv: str) -> list[str]:
    """Split a CSV --types value into a validated list (preserving spec order)."""
    raw = [t.strip().lower() for t in types_csv.split(",") if t.strip()]
    if not raw:
        raise click.ClickException(
            "--types is empty; pass a comma-separated subset of "
            f"{VALID_TYPES}, or omit to default to both."
        )
    bad = [t for t in raw if t not in VALID_TYPES]
    if bad:
        raise click.ClickException(
            f"unknown --types value(s) {bad}; valid values: {VALID_TYPES}"
        )
    seen = set(raw)
    return [t for t in VALID_TYPES if t in seen]


def _scope_prefix(
    *, all_vaults: bool, vault_uri: str | None, start_dir: Path | None
) -> str | None:
    """The `<vault-uri>/` prefix to scope the search to, or None for whole-profile.

    Precedence: `--all` (None) → explicit `--vault` → the vault we're standing
    in (walked up from start_dir / cwd). The trailing `/` makes it an exact
    subtree match and stops `my-notes` from also matching `my-notes-2`.
    """
    if all_vaults:
        return None
    if vault_uri:
        return vault_uri.rstrip("/") + "/"
    root = find_vault_root(start_dir or Path.cwd())
    if root is not None:
        uri = read_vault_uri(root)
        if uri:
            return uri.rstrip("/") + "/"
    return None


def cmd_search(
    query: str,
    *,
    profile: str | None,
    types_csv: str,
    vault_uri: str | None = None,
    all_vaults: bool = False,
    k: int,
    as_json: bool,
    directory: Path | None = None,
) -> int:
    types = _parse_types(types_csv)
    # None = both labels (the common case); otherwise restrict to one.
    labels = (
        None if len(types) == len(VALID_TYPES) else [t.capitalize() for t in types]
    )

    cfg_path = find_config_path()
    if cfg_path is None:
        raise click.ClickException("no ki config found — run `ki configure` first")
    cfg = load_config(cfg_path)
    prof = resolve_profile(cfg, profile, start_dir=directory)

    prefix = _scope_prefix(
        all_vaults=all_vaults, vault_uri=vault_uri, start_dir=directory
    )

    with driver_for(prof) as driver, driver.session() as session:
        try:
            rows = run_search(session, query, vault_prefix=prefix, labels=labels, k=k)
        except ClientError as exc:
            if "no such fulltext schema index" in str(exc).lower():
                raise click.ClickException(
                    "no search index found — run `ki index <vault>` first to build it."
                ) from exc
            raise

    if as_json:
        click.echo(json.dumps(rows, default=str, indent=2))
    else:
        _render_table(rows)
    return 0


def _render_table(rows: list[dict[str, Any]]) -> None:
    """Plain-text table — same Key:-header style as `ki outline`."""
    if not rows:
        console.print("[dim](no results)[/dim]")
        return
    console.print("Key:  D Document   S Section")
    console.print("")
    table = Table(show_header=True, header_style="bold")
    table.add_column("score", style="green", justify="right")
    table.add_column("T", justify="center")
    table.add_column("displayName")
    table.add_column("uri", style="dim")
    for r in rows:
        table.add_row(
            f"{(r.get('score') or 0):.2f}",
            TYPE_LETTER.get(r.get("label"), "?"),
            str(r.get("display_name") or ""),
            str(r.get("uri") or ""),
        )
    console.print(table)
