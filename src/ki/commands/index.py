"""`ki index <path>` — sync a folder of markdown into Neo4j."""

from __future__ import annotations

import logging
from pathlib import Path

import click
from rich.console import Console

from ..config import find_config_path, load_config
from ..ingest.pipeline import (
    IngestOptions,
    ingest_vault,
)
from .configure import configure as configure_flow

log = logging.getLogger(__name__)
console = Console()


def cmd_index(
    path: Path,
    *,
    profile: str | None,
    batch_size: int,
    max_file_size: int,
    concurrency: int,
    yes: bool,
) -> int:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise click.ClickException(f"path does not exist: {path}")
    if not path.is_dir():
        raise click.ClickException(f"path is not a directory: {path}")

    # Auto-sense: if no config, drop into configure (default = Local on --yes).
    cfg_path = find_config_path()
    if cfg_path is None:
        console.print("[yellow]No Neo4j connection configured.[/yellow]")
        configure_flow(yes=yes)
        cfg_path = find_config_path()

    cfg = load_config(cfg_path)
    try:
        prof = cfg.get_profile(profile)
    except KeyError as exc:
        raise click.ClickException(str(exc)) from exc

    opts = IngestOptions(
        profile=prof,
        batch_size=batch_size,
        concurrency=concurrency,
        max_file_size=max_file_size,
    )
    result = ingest_vault(path, opts)

    # Output summary.
    if result.vault_created:
        console.print(
            f"[green]✓[/green] Initialized vault at {path} "
            f"(id: {result.vault_uri[:8]}…)"
        )
    console.print(
        f"Indexed: [green]{result.docs_added}[/green] added, "
        f"[yellow]{result.docs_updated}[/yellow] updated, "
        f"[dim]{result.docs_skipped_unchanged} unchanged[/dim], "
        f"{result.sections_written} sections, "
        f"{result.links_written} links."
    )
    if result.docs_skipped_oversize:
        console.print(
            f"[red]Skipped {result.docs_skipped_oversize} oversize files "
            f"(> {max_file_size:,} bytes):[/red]"
        )
        for p in result.oversize_files[:20]:
            console.print(f"  {p}")
    if result.batch_shrunk_to:
        console.print(
            f"[yellow]Neo4j OOM mid-run — batch size shrunk to {result.batch_shrunk_to}."
            f" Consider passing --batch-size {result.batch_shrunk_to} next time.[/yellow]"
        )
    if not result.vault_description_set:
        marker = path / ".ki" / "vault.yaml"
        console.print(
            f"[yellow]⚠[/yellow]  This vault has no [bold]description[/bold] set. "
            f"Add one to [cyan]{marker}[/cyan] so agents can route searches across vaults:\n"
            f"    [dim]description: |\n"
            f"      One or two sentences on what's in this vault and when an "
            f"agent should pick it.[/dim]"
        )
    return 0
