"""`ki index <path>` — sync a folder of markdown into Neo4j."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.prompt import Prompt

from ..config import find_config_path, load_config
from ..ingest.pipeline import (
    IngestOptions,
    ingest_vault,
)
from ..vault import (
    VaultDescriptionExists,
    read_or_create_vault_id,
    vault_marker_path,
    write_vault_description,
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
    description: str | None = None,
    force_description: bool = False,
) -> int:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise click.ClickException(f"path does not exist: {path}")
    if not path.is_dir():
        raise click.ClickException(f"path is not a directory: {path}")

    if force_description and description is None:
        raise click.ClickException(
            "--force-description requires --description"
        )

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

    # Handle vault description before ingest. Two paths:
    #   --description "..."  → write it (refusing to clobber existing unless --force-description)
    #   first-run on a fresh marker with a TTY and no --yes → prompt
    # In both cases, `read_or_create_vault_id` ensures the marker exists so
    # `write_vault_description` has a file to round-trip through.
    marker_existed_before = vault_marker_path(path).exists()
    if description is not None:
        read_or_create_vault_id(path)
        try:
            write_vault_description(path, description, force=force_description)
        except VaultDescriptionExists as exc:
            raise click.ClickException(
                f"vault at {path} already has a description set "
                f"(\"{exc.existing[:60]}...\"). Pass --force-description to overwrite."
            ) from exc
        console.print(
            f"[green]✓[/green] Wrote vault description to "
            f"[cyan]{vault_marker_path(path)}[/cyan]."
        )
    elif (
        not marker_existed_before
        and not yes
        and sys.stdin.isatty()
    ):
        # First-time human user. Prompt — but don't block: empty input falls
        # through to the existing post-ingest warning.
        console.print(
            "\n[bold]This vault is being indexed for the first time.[/bold]"
        )
        console.print(
            "Add a one-sentence [bold]description[/bold] so agents know what "
            "this vault is for (or leave blank to skip)."
        )
        prompted = Prompt.ask("description", default="").strip()
        read_or_create_vault_id(path)
        if prompted:
            write_vault_description(path, prompted, force=False)
            console.print(
                f"[green]✓[/green] Wrote vault description to "
                f"[cyan]{vault_marker_path(path)}[/cyan]."
            )

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
