"""`ki search <query>` — fulltext retrieval over documents and sections.

One sweep over the shared `content_search` index (which covers
displayName + content + aliases + description at once).

Scope resolution has two modes, with `--profile` as the lever between them
(see docs/commands/search.md §5 and docs/scoping.md §3.2):

  - **Local (default)** — no `--profile`. Profile *and* vault come from the
    resolution dir's `.ki/vault.yaml` (cwd, or `-C <dir>`); not in a vault →
    error. Scope defaults to that vault; narrow with `--under <uri-or-path>`.
  - **Remote (`--profile`)** — "I'm not working on the vault I'm standing in."
    `--profile P` → all vaults in P; `--profile P --vault a,b` → those vaults;
    `--profile P --under <uri>` → one subtree (uri only — there's no local
    filesystem to resolve a path against).

`--vault` requires `--profile`. `--under` and `--vault` are mutually exclusive
(narrow one subtree, or pick whole vaults — not both). Every run prints a
one-line scope banner to stderr so the resolved profile/scope is never a
silent guess.

Flags:
  --types <csv>     Subset of {document,section} (default: both).
  --under <ref>     Narrow to a subtree: a uri anywhere, or a filesystem path
                    in the local vault. Mutually exclusive with --vault.
  --vault <csv>     Remote: limit to these vault uris (requires --profile).
  --k N             Result cap (default: 10).
  --json            Emit machine-readable JSON rows.
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
from ..vault import find_vault_root, read_vault_profile, read_vault_uri, slugify_path

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


_PATH_PREFIXES = ("/", "~", "./", "../")


def _looks_like_path(arg: str) -> bool:
    """Whether `arg` is an explicit filesystem path (vs. a bare uri).

    Only the unambiguous path forms count — leading `/`, `~`, `./`, `../`, or a
    lone `.`/`..`. A bare relative string (`api/v2`) is treated as a uri.
    """
    return arg.startswith(_PATH_PREFIXES) or arg in (".", "..")


def resolve_to_uri(
    arg: str, vault_uri: str, vault_path: Path, cwd: Path
) -> str:
    """Resolve a `--under` argument (a uri OR a filesystem path) to a uri.

    Local-only — `vault_uri`/`vault_path` describe the vault you're in:
      1. already a uri in this vault → returned as-is.
      2. an existing file/dir in this vault → its uri (`-N`-safe: the slug
         comes from the on-disk marker via `vault_uri`, not the basename).
      3. neither → a loud error, never a silent miss.
    """
    if arg == vault_uri or arg.startswith(vault_uri + "/"):
        return arg.rstrip("/")

    p = Path(arg).expanduser()
    if not p.is_absolute():
        p = cwd / p
    p = p.resolve()
    if p.exists() and p.is_relative_to(vault_path):
        rel = p.relative_to(vault_path).as_posix()
        return vault_uri if rel == "." else f"{vault_uri}/{slugify_path(rel)}"

    raise click.ClickException(
        f"--under {arg!r} is neither a uri in vault {vault_uri!r} nor a file/dir "
        f"inside it. Copy a uri from `ki outline`/`ki search`, or pass a path "
        f"under the vault."
    )


def _resolve_scope(
    cfg: Config,
    *,
    profile_flag: str | None,
    under_flag: str | None,
    vault_flag: str | None,
    start_dir: Path | None,
) -> tuple[Profile, list[str] | None, str]:
    """Resolve (profile, scope-uris, banner) for `ki search`.

    Returns a list of containment-root uris (or None = all vaults) for the
    `$scope` predicate. Two modes split on `--profile`; see module docstring.
    """
    search_dir = start_dir or Path.cwd()
    via_c = start_dir is not None  # the dir was pointed at with -C

    # --- flag-combination guards ---
    if vault_flag and not profile_flag:
        raise click.ClickException(
            "--vault requires --profile (it selects vaults in a profile you "
            "name explicitly). To narrow the vault you're in, use --under."
        )
    if under_flag and vault_flag:
        raise click.ClickException(
            "--under and --vault are mutually exclusive: --under narrows to one "
            "subtree, --vault picks whole vaults. Pick one."
        )

    # --- remote mode: --profile names the profile; --under or --vault scopes ---
    if profile_flag:
        try:
            prof = cfg.get_profile(profile_flag)
        except KeyError as exc:
            raise click.ClickException(str(exc)) from exc
        if under_flag:
            # No local vault to resolve a path against — a remote --under must
            # be a uri (self-addressing), taken verbatim.
            if _looks_like_path(under_flag):
                raise click.ClickException(
                    f"--under {under_flag!r} looks like a path, but with --profile "
                    f"there's no local vault to resolve it against. Pass a uri "
                    f"(e.g. --under my-vault/folder/doc.md)."
                )
            scope = under_flag.rstrip("/")
            scope_uris: list[str] | None = [scope]
            scope_part = f"under '{scope}'"
        elif vault_flag:
            scope_uris = [
                v.strip().rstrip("/") for v in vault_flag.split(",") if v.strip()
            ]
            if not scope_uris:
                raise click.ClickException("--vault is empty; pass one or more vault uris.")
            scope_part = f"vaults [{', '.join(scope_uris)}]"
        else:
            scope_uris = None
            scope_part = "all vaults"
        return prof, scope_uris, f"ki: profile '{prof.name}' · {scope_part}"

    # --- local mode: profile + vault from the resolution dir's .ki marker ---
    root = find_vault_root(search_dir)
    if root is None or not read_vault_profile(root):
        raise click.ClickException(
            "ki search needs a profile. Run inside a vault directory, point at "
            "one with -C <dir>, or pass --profile <name> for a remote profile."
        )
    bound = read_vault_profile(root)
    if bound not in cfg.profiles:
        raise click.ClickException(
            f"vault at {root} is bound to profile {bound!r}, which is not in "
            f"your config. Add it with `ki configure`, or re-bind to an "
            f"existing profile by re-indexing: `ki index . --profile <p>`."
        )
    prof = cfg.profiles[bound]
    vault_uri = read_vault_uri(root)

    if under_flag:
        scope = resolve_to_uri(under_flag, vault_uri, root, cwd=search_dir)
        scope_part = f"under '{scope}'"
    else:
        scope = vault_uri
        scope_part = f"vault '{scope}'"

    tag = f"  (-C {root})" if via_c else "  (from .ki)"
    return prof, [scope], f"ki: profile '{prof.name}' · {scope_part}{tag}"


def cmd_search(
    query: str,
    *,
    profile: str | None,
    types_csv: str,
    under: str | None = None,
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

    prof, scope_uris, banner = _resolve_scope(
        cfg, profile_flag=profile, under_flag=under, vault_flag=vault_uri,
        start_dir=directory,
    )
    click.echo(banner, err=True)

    with driver_for(prof) as driver, driver.session() as session:
        try:
            rows = run_search(session, query, scope_uris=scope_uris, labels=labels, k=k)
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
