"""`ki init <path>` — write `.ki/vault.yaml` without indexing.

Most users never run this. The usual flow is `ki index <path>`, which auto-
creates the marker. `init` exists for the narrow case of pre-creating a
marker that's committed to git before any content exists.

Connectivity: like `ki index`, this command needs a configured Neo4j
profile so the slug-collision check can run. (Pre-0.4.0 `ki init` was
offline because the URI was a random UUID; the slug scheme makes that
unsafe — without checking the graph, two vaults could pre-claim the same
slug and silently merge on first ingest.)
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from ..config import find_config_path, load_config
from ..neo4j_client import driver_for
from ..profile_resolve import resolve_profile
from ..vault import (
    compute_base_slug,
    find_next_vault_slug,
    read_vault_marker,
    write_vault_marker,
)

console = Console()


def cmd_init(path: Path, *, profile: str | None = None) -> int:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise click.ClickException(f"path is not a directory: {path}")

    existing = read_vault_marker(path)
    if existing is not None:
        console.print(
            f"[dim]Vault already initialized at {path} "
            f"(uri: {existing['uri']})[/dim]"
        )
        return 0

    cfg_path = find_config_path()
    if cfg_path is None:
        raise click.ClickException(
            "no ki config found — run `ki configure` first"
        )
    cfg = load_config(cfg_path)
    try:
        prof = resolve_profile(cfg, profile, start_dir=path)
    except KeyError as exc:
        raise click.ClickException(str(exc)) from exc

    base = compute_base_slug(path)
    with driver_for(prof) as driver, driver.session() as session:
        vault_uri = find_next_vault_slug(session, base)
    write_vault_marker(path, uri=vault_uri, profile=prof.name)
    console.print(
        f"[green]✓[/green] Initialized vault at {path} (uri: {vault_uri})"
    )
    return 0
