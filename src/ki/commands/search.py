"""`ki search <query>` — fulltext retrieval over documents and sections.

One sweep over the shared `content_search` index (which covers
displayName + content + aliases + description at once).

Scope resolution:
  - Profile is REQUIRED: `--profile`, else the `.ki/vault.yaml` of the
    resolution dir (cwd, or `-C <dir>`), else error. No `default_profile`
    fallback — `ki search` always tells you which profile it hit.
  - Vault is optional: `--vault`, else (when `--profile` overrides) all vaults,
    else the resolution dir's `.ki` vault, else all vaults.

Every run prints a one-line scope banner to stderr so the resolved
profile/vault is never a silent guess.

Flags:
  --types <csv>   Subset of {document,section} (default: both).
  --vault <uri>   Scope to a specific vault.
  --k N           Result cap (default: 10).
  --json          Emit machine-readable JSON rows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
from neo4j.exceptions import ClientError
from rich.console import Console
from rich.table import Table

from ..config import Config, Profile, find_config_path, load_config
from ..neo4j_client import driver_for
from ..search.queries import run_search
from ..vault import find_vault_root, read_vault_profile, read_vault_uri

console = Console()

VALID_TYPES = ("document", "section")
DEFAULT_TYPES = "document,section"
TYPE_LETTER = {"Document": "D", "Section": "S"}


def _parse_types(types_csv: str) -> list[str]:
    """Split a CSV --types value into a validated list (preserving spec order)."""
    raw = [t.strip().lower() for t in types_csv.split(",") if t.strip()]
    if not raw:
        raise click.ClickException(
            "--types is empty; pass a comma-separated subset of "
            f"{VALID_TYPES}, or omit to default to both."
        )
    bad = [t for t in raw if t not in VALID_TYPES]
    if bad:
        raise click.ClickException(
            f"unknown --types value(s) {bad}; valid values: {VALID_TYPES}"
        )
    seen = set(raw)
    return [t for t in VALID_TYPES if t in seen]


def _resolve_scope(
    cfg: Config,
    *,
    profile_flag: str | None,
    vault_flag: str | None,
    start_dir: Path | None,
) -> tuple[Profile, str | None, str]:
    """Resolve (profile, vault-prefix, banner) for `ki search`.

    Profile is REQUIRED (see module docstring). Vault scope precedence:
    `--vault` → (profile overridden → all vaults) → the resolution dir's `.ki`
    vault → all vaults. The trailing `/` on the prefix makes it an exact
    subtree match (so `my-notes` doesn't also match `my-notes-2`).
    """
    search_dir = start_dir or Path.cwd()
    root = find_vault_root(search_dir)
    via_c = start_dir is not None  # the dir was pointed at with -C

    # --- profile (required) ---
    if profile_flag:
        try:
            prof = cfg.get_profile(profile_flag)
        except KeyError as exc:
            raise click.ClickException(str(exc)) from exc
        prof_from = None
    elif root is not None and read_vault_profile(root):
        bound = read_vault_profile(root)
        if bound not in cfg.profiles:
            raise click.ClickException(
                f"vault at {root} is bound to profile {bound!r}, which is not in "
                f"your config. Re-bind with `ki use <profile>`, or create it with "
                f"`ki configure --profile {bound}`."
            )
        prof = cfg.profiles[bound]
        prof_from = root
    else:
        raise click.ClickException(
            "ki search needs a profile. Run inside a vault directory, point at "
            "one with -C <dir>, or pass --profile <name> (optionally --vault <uri>)."
        )

    # --- vault scope (optional) ---
    if vault_flag:
        scope_uri: str | None = vault_flag.rstrip("/")
        vault_from = None
    elif profile_flag:
        # Overriding the profile drops the .ki vault auto-scope: a vault lives
        # in one profile, so the dir's vault is meaningless under a new one.
        scope_uri = None
        vault_from = None
    elif root is not None and read_vault_uri(root):
        scope_uri = read_vault_uri(root)
        vault_from = root
    else:
        scope_uri = None
        vault_from = None

    prefix = f"{scope_uri}/" if scope_uri else None

    # --- banner (stderr) ---
    vault_part = f"vault '{scope_uri}'" if scope_uri else "all vaults"
    src = prof_from or vault_from
    tag = ""
    if src is not None:
        tag = f"  (-C {src})" if via_c else "  (from .ki)"
    banner = f"ki: profile '{prof.name}' · {vault_part}{tag}"
    return prof, prefix, banner


def cmd_search(
    query: str,
    *,
    profile: str | None,
    types_csv: str,
    vault_uri: str | None = None,
    k: int,
    as_json: bool,
    directory: Path | None = None,
) -> int:
    types = _parse_types(types_csv)
    # None = both labels (the common case); otherwise restrict to one.
    labels = (
        None if len(types) == len(VALID_TYPES) else [t.capitalize() for t in types]
    )

    cfg_path = find_config_path()
    if cfg_path is None:
        raise click.ClickException("no ki config found — run `ki configure` first")
    cfg = load_config(cfg_path)

    prof, prefix, banner = _resolve_scope(
        cfg, profile_flag=profile, vault_flag=vault_uri, start_dir=directory
    )
    click.echo(banner, err=True)

    with driver_for(prof) as driver, driver.session() as session:
        try:
            rows = run_search(session, query, vault_prefix=prefix, labels=labels, k=k)
        except ClientError as exc:
            if "no such fulltext schema index" in str(exc).lower():
                raise click.ClickException(
                    "no search index found — run `ki index <vault>` first to build it."
                ) from exc
            raise

    if as_json:
        click.echo(json.dumps(rows, default=str, indent=2))
    else:
        _render_table(rows)
    return 0


def _render_table(rows: list[dict[str, Any]]) -> None:
    """Plain-text table — same Key:-header style as `ki outline`."""
    if not rows:
        console.print("[dim](no results)[/dim]")
        return
    console.print("Key:  D Document   S Section")
    console.print("")
    table = Table(show_header=True, header_style="bold")
    table.add_column("score", style="green", justify="right")
    table.add_column("T", justify="center")
    table.add_column("displayName")
    table.add_column("uri", style="dim")
    for r in rows:
        table.add_row(
            f"{(r.get('score') or 0):.2f}",
            TYPE_LETTER.get(r.get("label"), "?"),
            str(r.get("display_name") or ""),
            str(r.get("uri") or ""),
        )
    console.print(table)
