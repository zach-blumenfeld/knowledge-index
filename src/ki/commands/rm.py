"""`ki rm <path>` — remove nodes from the index. Source files untouched.

Blast-radius scaling (docs/requirements_v01_mvp.md *Removal*):
  - single doc       → no prompt
  - subtree (dir)    → prompt with count, suppressed by --yes
  - whole vault      → requires --vault flag AND typed display-name confirm
  - never            → there is no --purge

Flags:
  --vault          require this for whole-vault removal
  --dry-run        report only; no Neo4j writes
  --yes            skip prompts (scripts / agent auto-mode)
  --keep-marker    on --vault, preserve `.ki/vault.yaml` (reset idiom)
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.prompt import Confirm, Prompt

from ..config import find_config_path, load_config
from ..ingest import queries as Q
from ..neo4j_client import driver_for
from ..vault import (
    document_uri,
    read_vault_marker,
    remove_vault_marker,
    slugify_path,
)

console = Console()


def cmd_rm(
    target: str,
    *,
    profile: str | None,
    vault_flag: bool,
    dry_run: bool,
    yes: bool,
    keep_marker: bool,
) -> int:
    cfg_path = find_config_path()
    if cfg_path is None:
        raise click.ClickException("no ki config found — run `ki configure` first")
    cfg = load_config(cfg_path)
    prof = cfg.get_profile(profile)

    if vault_flag:
        return _rm_vault(
            target, prof,
            dry_run=dry_run, yes=yes, keep_marker=keep_marker,
        )

    p = Path(target).expanduser().resolve()
    if not p.exists():
        raise click.ClickException(f"path not found: {p}")

    # Walk up to find the vault root (.ki/vault.yaml marker).
    vault_root = _find_vault_root(p)
    if vault_root is None:
        raise click.ClickException(
            f"no .ki/vault.yaml found above {p}. If you want to remove an entire "
            f"vault by URI, use `ki rm <vault-uri> --vault`."
        )
    marker_data = read_vault_marker(vault_root)
    if marker_data is None or not marker_data.get("uri"):
        raise click.ClickException(f"vault marker at {vault_root}/.ki/vault.yaml is empty")
    vault_uri = str(marker_data["uri"]).strip()

    if p.is_file():
        return _rm_document(p, vault_root, vault_uri, prof, dry_run=dry_run)
    return _rm_subtree(p, vault_root, vault_uri, prof, dry_run=dry_run, yes=yes)


def _find_vault_root(p: Path) -> Path | None:
    cur = p if p.is_dir() else p.parent
    for _ in range(20):
        if (cur / ".ki" / "vault.yaml").exists():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def _rm_document(file_path: Path, vault_root: Path, vault_uri: str, profile, *, dry_run: bool) -> int:
    rel = file_path.relative_to(vault_root)
    doc_uri = document_uri(vault_uri, rel)
    if dry_run:
        console.print(f"[yellow]dry-run[/yellow] would remove document {doc_uri}")
        return 0
    with driver_for(profile) as driver:
        with driver.session() as session:
            session.run(Q.DELETE_DOCUMENT_AND_SECTIONS, docUri=doc_uri).consume()
    console.print(f"[green]✓[/green] removed document {doc_uri}")
    return 0


def _rm_subtree(
    dir_path: Path,
    vault_root: Path,
    vault_uri: str,
    profile,
    *,
    dry_run: bool,
    yes: bool,
) -> int:
    rel = dir_path.relative_to(vault_root)
    rel_str = rel.as_posix()
    if rel_str in (".", ""):
        raise click.ClickException(
            "removing the vault root requires --vault and typed confirmation"
        )
    prefix = f"{vault_uri}/{slugify_path(rel_str)}/"

    with driver_for(profile) as driver:
        with driver.session() as session:
            row = session.run(Q.COUNT_SUBTREE, uriPrefix=prefix).single()
            count = row["doc_count"] if row else 0
            if count == 0:
                console.print(f"[dim](no documents under {rel_str} in the index)[/dim]")
                return 0
            console.print(
                f"This will remove [yellow]{count}[/yellow] document(s) under "
                f"[cyan]{rel_str}[/cyan] from the index. Source files untouched."
            )
            if dry_run:
                console.print("[yellow]dry-run[/yellow] no changes made.")
                return 0
            if not yes and not Confirm.ask("Proceed?", default=False):
                console.print("[yellow]Cancelled.[/yellow]")
                return 1
            session.run(Q.DELETE_SUBTREE, uriPrefix=prefix).consume()
    console.print(f"[green]✓[/green] removed {count} document(s) under {rel_str}")
    return 0


def _rm_vault(
    target: str,
    profile,
    *,
    dry_run: bool,
    yes: bool,
    keep_marker: bool,
) -> int:
    """Remove an entire vault. `target` may be a directory path or a Vault.uri slug."""
    # Prefer the path interpretation if the target resolves to an existing
    # directory with a marker. Otherwise treat the literal string as a
    # Vault.uri slug — leave `p` as None so post-delete marker cleanup is
    # skipped.
    candidate = Path(target).expanduser()
    p: Path | None
    vault_uri: str | None
    if candidate.exists() and candidate.is_dir():
        p = candidate.resolve()
        marker = read_vault_marker(p)
        if marker is None:
            raise click.ClickException(
                f"{p} has no .ki/vault.yaml — if you want to remove a vault "
                f"by its URI, pass the URI directly (e.g. 'my-notes')."
            )
        vault_uri = str(marker["uri"]).strip()
    else:
        p = None
        vault_uri = target.strip() or None
    if not vault_uri:
        raise click.ClickException(
            f"could not resolve vault from {target!r} — pass a Vault.uri slug "
            f"or a directory containing .ki/vault.yaml"
        )

    with driver_for(profile) as driver:
        with driver.session() as session:
            row = session.run(Q.COUNT_VAULT, vaultUri=vault_uri).single()
            if not row or row.get("display_name") is None:
                raise click.ClickException(f"vault {vault_uri} not found in the index")
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

            session.run(Q.DELETE_VAULT, vaultUri=vault_uri).consume()

    # Marker cleanup (path-form only).
    if p and not keep_marker:
        remove_vault_marker(p)
        console.print(f"[green]✓[/green] removed vault marker at {p}/.ki/vault.yaml")
    elif p and keep_marker:
        console.print(
            f"[dim]Marker preserved at {p}/.ki/vault.yaml "
            f"— next `ki index` will rebuild this vault under the same uri.[/dim]"
        )

    console.print(f"[green]✓[/green] removed vault {display_name} ({vault_uri})")
    return 0


