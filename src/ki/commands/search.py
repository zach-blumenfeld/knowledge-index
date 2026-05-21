"""`ki search <query>` — fulltext retrieval across documents, sections, and vaults.

Flags:
  --types <csv>   Subset of {document,section,vault} (default: all three).
                  e.g. --types section,document
  --k N           Total result cap across all selected types (default: 10).
  --json          Emit machine-readable JSON. Heterogeneous list — each row
                  keeps its native B.1 / B.2 / B.11 shape; key off the
                  `document_uri` / `section_uri` / `vault_uri` field to
                  identify the type.

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

VALID_TYPES = ("document", "section", "vault")
DEFAULT_TYPES = "document,section,vault"
TYPE_LETTER = {"Document": "D", "Section": "S", "Vault": "V"}


def _parse_types(types_csv: str) -> list[str]:
    """Split a CSV --types value into a validated list (preserving spec order)."""
    raw = [t.strip().lower() for t in types_csv.split(",") if t.strip()]
    if not raw:
        raise click.ClickException(
            "--types is empty; pass a comma-separated subset of "
            f"{VALID_TYPES}, or omit to default to all."
        )
    bad = [t for t in raw if t not in VALID_TYPES]
    if bad:
        raise click.ClickException(
            f"unknown --types value(s) {bad}; valid values: {VALID_TYPES}"
        )
    # Preserve the order documented in VALID_TYPES (so dispatch + tests are deterministic).
    seen = set(raw)
    return [t for t in VALID_TYPES if t in seen]


def _warn_missing_vault_description(rows: list[dict[str, Any]]) -> None:
    """One-line stderr warning per vault row whose description is null/empty.

    Hint that the user (or routing agent) should set `description:` in
    `.ki/vault.yaml` so future vault searches are more discriminative.
    """
    for r in rows:
        if r.get("label") != "Vault":
            continue
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
    types_csv: str,
    k: int,
    as_json: bool,
) -> int:
    types = _parse_types(types_csv)

    cfg_path = find_config_path()
    if cfg_path is None:
        raise click.ClickException("no ki config found — run `ki configure` first")
    cfg = load_config(cfg_path)
    prof = cfg.get_profile(profile)

    rows: list[dict[str, Any]] = []
    with driver_for(prof) as driver, driver.session() as session:
        # Each query is capped at k; the merged list is re-sorted by score
        # and capped to k again. Cross-type fulltext scores are not strictly
        # comparable (term-frequency normalization varies with set size), but
        # they're a useful single-axis ranking heuristic for surfacing top-k.
        if "document" in types:
            for r in run_b1(session, query, k=k):
                r["label"] = "Document"
                rows.append(r)
        if "section" in types:
            for r in run_b2(session, query, k=k):
                r["label"] = "Section"
                rows.append(r)
        if "vault" in types:
            for r in run_vault_search(session, query, k=k):
                r["label"] = "Vault"
                rows.append(r)

    rows.sort(key=lambda r: r.get("score") or 0.0, reverse=True)
    rows = rows[:k]

    if as_json:
        click.echo(json.dumps(rows, default=str, indent=2))
    else:
        _render_table(rows)
    if "vault" in types:
        _warn_missing_vault_description(rows)
    return 0


def _unify(row: dict[str, Any]) -> dict[str, Any]:
    """Pick the display-relevant fields per label for the unified table.

    Returns {score, label, displayName, uri}. Source columns differ by
    label (B.1 uses `title` + `document_uri`, B.2 uses `heading` +
    `section_uri`, B.11 uses `display_name` + `vault_uri`) — this is the
    single place that knows the mapping.
    """
    label = row.get("label")
    if label == "Document":
        return {
            "score": row.get("score"),
            "label": label,
            "displayName": row.get("title") or "",
            "uri": row.get("document_uri") or "",
        }
    if label == "Section":
        return {
            "score": row.get("score"),
            "label": label,
            "displayName": row.get("heading") or "",
            "uri": row.get("section_uri") or "",
        }
    if label == "Vault":
        return {
            "score": row.get("score"),
            "label": label,
            "displayName": row.get("display_name") or row.get("name") or "",
            "uri": row.get("vault_uri") or "",
        }
    return {
        "score": row.get("score"),
        "label": label or "?",
        "displayName": "",
        "uri": "",
    }


def _render_table(rows: list[dict[str, Any]]) -> None:
    """Plain-text unified table — same Key:-header style as `ki tree`."""
    if not rows:
        console.print("[dim](no results)[/dim]")
        return
    # Key line first (matches ki tree's convention so the type letters
    # transfer between the two outputs).
    console.print("Key:  V Vault   D Document   S Section")
    console.print("")
    table = Table(show_header=True, header_style="bold")
    table.add_column("score", style="green", justify="right")
    table.add_column("T", justify="center")
    table.add_column("displayName")
    table.add_column("uri", style="dim")
    for r in rows:
        u = _unify(r)
        table.add_row(
            f"{u['score'] or 0:.2f}",
            TYPE_LETTER.get(u["label"], "?"),
            str(u["displayName"]),
            str(u["uri"]),
        )
    console.print(table)
