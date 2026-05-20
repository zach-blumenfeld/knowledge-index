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
    vault_marker_path,
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

    # Vault description: --description flag is canonical; otherwise prompt
    # the first-time human user. ingest_vault writes both the URI and the
    # description into `.ki/vault.yaml` in one shot after slug assignment
    # (which needs the Neo4j session), so we just collect the desired value
    # here and pass it through IngestOptions.
    marker_existed_before = vault_marker_path(path).exists()
    effective_description: str | None = description
    if (
        description is None
        and not marker_existed_before
        and not yes
        and sys.stdin.isatty()
    ):
        # First-time human user. Empty input is fine — falls through to the
        # post-ingest warning that nudges the user to add one.
        console.print(
            "\n[bold]This vault is being indexed for the first time.[/bold]"
        )
        console.print(
            "Add a one-sentence [bold]description[/bold] so agents know what "
            "this vault is for (or leave blank to skip)."
        )
        prompted = Prompt.ask("description", default="").strip()
        if prompted:
            effective_description = prompted

    opts = IngestOptions(
        profile=prof,
        batch_size=batch_size,
        concurrency=concurrency,
        max_file_size=max_file_size,
        description=effective_description,
        force_description=force_description,
    )
    try:
        result = ingest_vault(path, opts)
    except VaultDescriptionExists as exc:
        raise click.ClickException(
            f"vault at {path} already has a description set "
            f"(\"{exc.existing[:60]}...\"). Pass --force-description to overwrite."
        ) from exc

    # Output summary.
    if result.vault_created:
        console.print(
            f"[green]✓[/green] Initialized vault at {path} "
            f"(uri: {result.vault_uri})"
        )
    console.print(
        f"Indexed: [green]{result.docs_added}[/green] added, "
        f"[yellow]{result.docs_updated}[/yellow] updated, "
        f"[dim]{result.docs_skipped_unchanged} unchanged[/dim], "
        f"{result.sections_written} sections, "
        f"{result.folders_total} folders, "
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
