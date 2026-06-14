"""`ki configure` — interactive wizard for a Neo4j connection profile.

Three paths:
  1) Local      → wraps `podman` to run `neo4j:latest` locally.
                  See skills/knowledge-base/references/neo4j-podman.md for the full runbook.
  2) Aura       → wraps `neo4j-cli aura create` (cloud — billable).
  3) Existing   → prompt for URI + credentials.

On `--yes` (agent auto-mode escape hatch), pick the default (Local) and run
non-interactively. *Aura is never auto-picked* per docs/requirements_v01_mvp.md
*Agent auto-mode behavior* — it creates billable resources.
"""

from __future__ import annotations

import logging
import shutil

import click
from rich.console import Console
from rich.prompt import Confirm, Prompt

from .. import neo4j_podman
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
            "  [cyan]1) Local (neo4j w/ podman)[/cyan] → runs `neo4j:latest`"
            " in a local Podman container (APOC + GenAI plugins)"
        )
        console.print(
            "     [dim]Best for: solo work on this laptop[/dim]"
        )
        console.print(
            "  [cyan]2) Aura[/cyan]                    → wraps `neo4j-cli aura create`"
            " ([red]billable cloud resource[/red])"
        )
        console.print(
            "     [dim]Best for: sharing an index across machines or a team[/dim]"
        )
        console.print(
            "  [cyan]3) Existing[/cyan]                → prompt for URI + credentials"
        )
        console.print(
            "     [dim]Best for: pointing at a Neo4j you already run[/dim]\n"
        )
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
    if not neo4j_podman.is_installed():
        raise click.ClickException(
            "`podman` is not installed. See skills/knowledge-base/references/neo4j-podman.md "
            "(Preflight) for install steps — on macOS: `brew install podman` "
            "then `podman machine init && podman machine start`."
        )
    state = neo4j_podman.container_state()
    if state == "missing":
        console.print(
            "[dim]Starting Neo4j via podman "
            f"(image: {neo4j_podman.IMAGE}, first-run pulls the image)...[/dim]"
        )
    elif state == "stopped":
        console.print(
            f"[dim]Starting existing `{neo4j_podman.CONTAINER_NAME}` container...[/dim]"
        )
    else:
        console.print(
            f"[dim]`{neo4j_podman.CONTAINER_NAME}` is already running.[/dim]"
        )
    try:
        creds = neo4j_podman.ensure_running()
    except neo4j_podman.PodmanError as exc:
        raise click.ClickException(str(exc)) from exc
    console.print(f"[green]✓[/green] Neo4j ready at {creds.uri}")
    return Profile(
        name=name,
        uri=creds.uri,
        user=creds.user,
        password=creds.password,
        source="local-podman",
        database="neo4j",  # the container's default db; known and safe to pin.
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
    # Blank → use the server's home database. Don't default to "neo4j": that
    # name doesn't exist on Aura Free (home db is the instance DBID).
    database = Prompt.ask(
        "Database [blank = server's home database]", default=""
    ).strip() or None
    return Profile(
        name=name,
        uri=uri,
        user=user,
        password=password,
        source="existing",
        database=database,
    )
