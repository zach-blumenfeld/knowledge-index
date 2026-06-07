"""`ki index <path>` — sync a folder of markdown into Neo4j."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.prompt import Prompt

from ..config import find_config_path, load_config
from ..ingest.pipeline import (
    IngestOptions,
    IngestServiceUnavailable,
    ingest_vault,
)
from ..vault import (
    VaultDescriptionExists,
    vault_marker_path,
)
from .configure import configure as configure_flow

log = logging.getLogger(__name__)
console = Console()


class _RichProgressReporter:
    """Rich-backed `ProgressReporter` for interactive `ki index` runs (#53).

    Three sequential tasks match the ingest phases: reading, processing
    docs (with running added/updated/skipped counts), and a finalize spinner.
    """

    def __init__(self, console: Console) -> None:
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        )
        self._read_task: int | None = None
        self._read_total: int = 0
        self._docs_task: int | None = None
        self._finalize_task: int | None = None
        self._counts = {"added": 0, "updated": 0, "skipped": 0}

    def __enter__(self) -> _RichProgressReporter:
        self._progress.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._progress.stop()

    def reading_start(self, total: int) -> None:
        self._read_total = total
        self._read_task = self._progress.add_task("Reading files", total=total)

    def reading_advance(self, n: int = 1) -> None:
        if self._read_task is not None:
            self._progress.update(self._read_task, advance=n)

    def reading_done(self) -> None:
        if self._read_task is not None:
            self._progress.update(self._read_task, completed=self._read_total)

    def docs_start(self, total: int) -> None:
        self._docs_task = self._progress.add_task(
            "Processing docs", total=total
        )

    def doc_processed(self, kind: str) -> None:
        if kind in self._counts:
            self._counts[kind] += 1
        if self._docs_task is not None:
            self._progress.update(
                self._docs_task,
                advance=1,
                description=(
                    f"Processing docs ("
                    f"added {self._counts['added']}, "
                    f"updated {self._counts['updated']}, "
                    f"skipped {self._counts['skipped']})"
                ),
            )

    def docs_done(self) -> None:
        pass

    def finalize_start(self) -> None:
        self._finalize_task = self._progress.add_task(
            "Finalizing links + aliases", total=None
        )

    def finalize_done(self) -> None:
        if self._finalize_task is not None:
            self._progress.update(
                self._finalize_task, completed=1, total=1
            )


def _render_service_unavailable(
    exc: IngestServiceUnavailable, *, batch_size: int
) -> None:
    """Render a Profile.source-aware recovery hint block for an ingest crash.

    #54 Fix 3. `local-podman` profiles get the canonical `neo4j-ki` container
    commands and a pointer to `skills/knowledge-index/references/neo4j-podman.md` *Recovery*; other
    profiles get generic heap/batch/split guidance since we don't know their
    container shape.
    """
    console.print(
        f"[red]✗[/red] Neo4j connection lost after "
        f"[bold]{exc.docs_processed}[/bold] / {exc.docs_total} docs.",
    )
    console.print(
        "[dim]The database stopped responding mid-ingest — most often this "
        "means Neo4j ran out of memory.[/dim]\n"
    )
    if exc.profile_source == "local-podman":
        console.print("[bold]Diagnose the container:[/bold]")
        console.print("  [cyan]podman ps -a --filter name=neo4j-ki[/cyan]")
        console.print("  [cyan]podman logs --tail 80 neo4j-ki[/cyan]\n")
        console.print("[bold]If the container stopped, restart it:[/bold]")
        console.print("  [cyan]podman start neo4j-ki[/cyan]")
        console.print(
            "  [cyan]podman exec neo4j-ki cypher-shell -u neo4j -p password "
            "'RETURN 1'[/cyan]  (wait until this returns)\n"
        )
        console.print(
            "[bold]Then retry the index.[/bold] Full recovery flow + heap "
            "tuning notes live in [cyan]skills/knowledge-index/references/neo4j-podman.md[/cyan] "
            "(Recovery — graph went away)."
        )
    else:
        console.print("[bold]Likely fixes:[/bold]")
        console.print(
            f"  • Lower [cyan]--batch-size[/cyan] (currently {batch_size}); "
            "try half."
        )
        console.print(
            "  • Increase the Neo4j JVM heap (e.g. "
            "[cyan]NEO4J_server_memory_heap_max__size=4G[/cyan] for "
            "Docker/Podman setups)."
        )
        console.print(
            "  • Split the vault into smaller subsets across multiple "
            "[cyan]ki index[/cyan] runs."
        )


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
    chunk_size: int = 1000,
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

    # TTY-only progress reporter (#53). Non-interactive runs (CI, redirected
    # stdout) get no bar — keeps logs clean.
    use_progress = sys.stdout.isatty()
    reporter: _RichProgressReporter | None = (
        _RichProgressReporter(console) if use_progress else None
    )

    opts = IngestOptions(
        profile=prof,
        batch_size=batch_size,
        concurrency=concurrency,
        max_file_size=max_file_size,
        description=effective_description,
        force_description=force_description,
        chunk_size=chunk_size,
        progress=reporter,
    )
    try:
        if reporter is not None:
            with reporter:
                result = ingest_vault(path, opts)
        else:
            result = ingest_vault(path, opts)
    except VaultDescriptionExists as exc:
        raise click.ClickException(
            f"vault at {path} already has a description set "
            f"(\"{exc.existing[:60]}...\"). Pass --force-description to overwrite."
        ) from exc
    except IngestServiceUnavailable as exc:
        _render_service_unavailable(exc, batch_size=batch_size)
        raise click.ClickException("Neo4j connection lost mid-ingest") from exc

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
