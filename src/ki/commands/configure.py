"""`ki configure` — interactive wizard for a Neo4j connection profile.

Three paths:
  1) Local      → wraps `neo4j-local` (no Docker, plugins pre-installed).
  2) Aura       → wraps `neo4j-cli aura create` (cloud — billable).
  3) Existing   → prompt for URI + credentials.

On `--yes` (agent auto-mode escape hatch), pick the default (Local) and run
non-interactively. *Aura is never auto-picked* per docs/requirements.md
*Agent auto-mode behavior* — it creates billable resources.
"""

from __future__ import annotations

import logging
import shutil

import click
from rich.console import Console
from rich.prompt import Confirm, Prompt

from .. import neo4j_local
from ..config import (
    Profile,
    default_config_path,
    find_config_path,
    load_config,
    save_config,
)
from ..neo4j_client import verify_connectivity

log = logging.getLogger(__name__)

console = Console()


def configure(
    *,
    profile_name: str | None = None,
    yes: bool = False,
    set_default: bool = False,
) -> Profile:
    """Run the configure flow. Returns the saved Profile."""
    cfg_path = find_config_path() or default_config_path()
    cfg = load_config(cfg_path)

    name = profile_name or ("default" if not cfg.profiles else None)
    if not name:
        if yes:
            name = "default"
        else:
            name = Prompt.ask("Profile name", default="default")

    console.print()
    if cfg_path.exists() and name in cfg.profiles:
        if yes:
            console.print(
                f"[yellow]Overwriting existing profile '{name}'.[/yellow]"
            )
        elif not Confirm.ask(
            f"Profile '{name}' already exists in {cfg_path}. Overwrite?",
            default=False,
        ):
            console.print("[yellow]Cancelled.[/yellow]")
            raise click.Abort()

    if yes:
        choice = "1"
    else:
        console.print("[bold]No Neo4j connection found. Set one up?[/bold]\n")
        console.print(
            "  [cyan]1) Local[/cyan]    → wraps `neo4j-local`"
            " (no Docker; APOC + GDS + GenAI plugins by default)"
        )
        console.print(
            "  [cyan]2) Aura[/cyan]     → wraps `neo4j-cli aura create`"
            " ([red]billable cloud resource[/red])"
        )
        console.print("  [cyan]3) Existing[/cyan] → prompt for URI + credentials\n")
        choice = Prompt.ask("Choice", choices=["1", "2", "3"], default="1")

    if choice == "1":
        profile = _configure_local(name)
    elif choice == "2":
        profile = _configure_aura(name, yes=yes)
    else:
        profile = _configure_existing(name)

    # Verify connectivity before saving.
    try:
        verify_connectivity(profile)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Failed to connect to {profile.uri}: {exc}[/red]")
        if not yes and not Confirm.ask("Save the profile anyway?", default=False):
            raise click.Abort() from exc

    cfg.add_profile(profile, set_default=set_default or (len(cfg.profiles) == 0))
    path = save_config(cfg, cfg_path)
    console.print(
        f"\n[green]✓[/green] Wrote profile [bold]{profile.name}[/bold] to {path}"
    )
    return profile


def _configure_local(name: str) -> Profile:
    if not neo4j_local.is_installed():
        raise click.ClickException(
            "`neo4j-local` is not installed. Install it from "
            "https://github.com/johnymontana/neo4j-local, then re-run."
        )
    console.print("[dim]Starting neo4j-local (downloads Neo4j + JRE on first run)...[/dim]")
    neo4j_local.start()
    creds = neo4j_local.credentials()
    console.print(f"[green]✓[/green] Started Neo4j locally at {creds.uri}")
    return Profile(
        name=name,
        uri=creds.uri,
        user=creds.user,
        password=creds.password,
        source="neo4j-local",
    )


def _configure_aura(name: str, *, yes: bool) -> Profile:
    if yes:
        # Auto-mode must NOT auto-create Aura instances per requirements.md.
        raise click.ClickException(
            "Aura provisioning creates a billable cloud resource and is "
            "never auto-picked. Re-run without --yes to use Aura."
        )
    if not shutil.which("neo4j-cli"):
        raise click.ClickException(
            "`neo4j-cli` is not installed. Install it from "
            "https://github.com/neo4j-labs/neo4j-cli, then re-run."
        )
    console.print(
        "[yellow]Aura provisioning creates a real, billable Neo4j Aura instance.[/yellow]"
    )
    if not Confirm.ask("Proceed?", default=False):
        raise click.Abort()
    # We don't pretend to know the exact `neo4j-cli aura create` UX — defer
    # to the user to walk through it. Then collect URI + creds.
    console.print(
        "[dim]Run `neo4j-cli aura create` in another shell to provision the "
        "instance, then come back here.[/dim]"
    )
    return _configure_existing(name)


def _configure_existing(name: str) -> Profile:
    uri = Prompt.ask("Neo4j URI", default="bolt://localhost:7687")
    user = Prompt.ask("User", default="neo4j")
    password = Prompt.ask("Password", password=True)
    return Profile(name=name, uri=uri, user=user, password=password, source="existing")
