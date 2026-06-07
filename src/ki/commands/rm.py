"""`ki rm <vault>` — remove an entire vault from the index. Source files untouched.

Vault-only by design. See `docs/index_rm_behavior.md` for the model: ki keeps
the vault as the only unit of sync, so `ki rm` operates only on vault-level
targets. Passing a file path, subdirectory, or any other granularity errors
with a message that points at `ki index` (the only way to re-sync individual
content).

Flags:
  --yes            skip the typed-display-name confirm (scripts / agent auto-mode)
  --keep-marker    leave .ki/vault.yaml on disk for the reset-and-rebuild idiom
  --dry-run        report counts; make no changes
  --chunk-size N   rows per batched-remove transaction (default 1000; lower on OOM)
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.prompt import Prompt

from ..config import find_config_path, load_config
from ..ingest import queries as Q
from ..ingest.remove import DEFAULT_CHUNK_SIZE, remove_vault
from ..neo4j_client import driver_for
from ..profile_resolve import resolve_profile
from ..vault import read_vault_marker, remove_vault_marker, vault_marker_path

console = Console()

NON_VAULT_TARGET_MESSAGE = (
    "ki rm only operates on vaults. Individual folders, documents, and "
    "sections sync at the vault level to mirror what's currently on disk — "
    "use `ki index <vault>` to refresh, or `ki rm <vault>` to remove the "
    "whole vault."
)


def cmd_rm(
    target: str,
    *,
    profile: str | None,
    dry_run: bool,
    yes: bool,
    keep_marker: bool,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> int:
    cfg_path = find_config_path()
    if cfg_path is None:
        raise click.ClickException("no ki config found — run `ki configure` first")
    cfg = load_config(cfg_path)

    vault_uri, vault_root = _resolve_vault_target(target)
    prof = resolve_profile(cfg, profile, start_dir=vault_root)

    with driver_for(prof) as driver, driver.session() as session:
        row = session.run(Q.COUNT_VAULT, vaultUri=vault_uri).single()
        if not row or row.get("display_name") is None:
            raise click.ClickException(f"vault {vault_uri!r} not found in the index")
        display_name = row["display_name"]
        doc_count = row["doc_count"]
        section_count = row["section_count"]

        console.print(
            f"This will remove vault [bold red]{display_name}[/bold red]\n"
            f"  uri:      {vault_uri}\n"
            f"  documents:{doc_count}\n"
            f"  sections: {section_count}\n"
            f"  source files: [green]untouched[/green]"
        )
        if dry_run:
            console.print("[yellow]dry-run[/yellow] no changes made.")
            return 0
        if not yes:
            typed = Prompt.ask(
                f"Type the vault display-name [yellow]{display_name}[/yellow] to confirm"
            )
            if typed != display_name:
                console.print("[yellow]Confirmation mismatch — cancelled.[/yellow]")
                return 1

        stats = remove_vault(session, vault_uri, chunk_size=chunk_size)

    if stats["orphans_removed"]:
        console.print(
            f"[dim]Garbage-collected {stats['orphans_removed']} orphaned external "
            f"link target(s).[/dim]"
        )

    # Marker cleanup (only meaningful when we resolved a real on-disk vault root).
    if vault_root is not None and not keep_marker:
        remove_vault_marker(vault_root)
        console.print(
            f"[green]✓[/green] removed vault marker at {vault_marker_path(vault_root)}"
        )
    elif vault_root is not None and keep_marker:
        console.print(
            f"[dim]Marker preserved at {vault_marker_path(vault_root)} — "
            f"next `ki index` will rebuild this vault under the same uri.[/dim]"
        )

    console.print(f"[green]✓[/green] removed vault {display_name} ({vault_uri})")
    return 0


def _resolve_vault_target(target: str) -> tuple[str, Path | None]:
    """Resolve `target` to (vault_uri, vault_root_or_None).

    Accepts:
      - a path to a vault root (has `.ki/vault.yaml`) → returns (uri, path)
      - a Vault.uri slug (no on-disk path) → returns (uri, None)

    Errors on anything else (file path, non-vault directory, etc.) with a
    message pointing at `ki index` as the right tool for sub-vault sync.
    """
    candidate = Path(target).expanduser()
    if candidate.exists():
        # On-disk target. The only valid shape is a directory that IS the
        # vault root.
        if candidate.is_file():
            raise click.ClickException(
                f"{target!r} is a file. {NON_VAULT_TARGET_MESSAGE}"
            )
        if not candidate.is_dir():
            raise click.ClickException(f"{target!r} is not a directory")
        marker = read_vault_marker(candidate)
        if marker is None:
            # Directory exists but isn't a vault root. Either the user pointed
            # at a subdirectory inside a vault, or at an unrelated directory.
            # Both error the same way — sub-vault granularity isn't supported.
            raise click.ClickException(
                f"{target!r} is not a vault root (no .ki/vault.yaml here). "
                f"{NON_VAULT_TARGET_MESSAGE}"
            )
        return str(marker["uri"]).strip(), candidate.resolve()

    # Off-disk target: treat as a literal Vault.uri slug.
    slug = target.strip()
    if not slug:
        raise click.ClickException(
            "empty `ki rm` target — pass a vault root path or a Vault.uri slug"
        )
    return slug, None
