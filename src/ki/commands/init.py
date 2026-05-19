"""`ki init <path>` — thin alias for writing `.ki/vault.yaml` without indexing.

Most users never run this. The usual flow is `ki index <path>`, which auto-
creates the marker. `init` exists for the narrow case of pre-creating a
marker that's committed to git before any content exists.
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from ..vault import read_or_create_vault_id

console = Console()


def cmd_init(path: Path) -> int:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise click.ClickException(f"path is not a directory: {path}")
    vault_id, created = read_or_create_vault_id(path)
    if created:
        console.print(
            f"[green]✓[/green] Initialized vault at {path} (id: {vault_id[:8]}…)"
        )
    else:
        console.print(
            f"[dim]Vault already initialized at {path} (id: {vault_id[:8]}…)[/dim]"
        )
    return 0
