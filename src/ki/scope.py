"""Shared scope resolution — turn a path-or-uri target into a uri.

Used by every command that takes a `uri-or-path` scope argument (`ki search
--under`, `ki rm`, and the local-only `ki add`/`ki mv`). Kept here, not in a
command module, so the write surface and the read surface resolve targets the
*same* way (see `docs/scoping.md` and `docs/commands/search.md` §5).
"""

from __future__ import annotations

from pathlib import Path

import click

from .vault import slugify_path

_PATH_PREFIXES = ("/", "~", "./", "../")


def looks_like_path(arg: str) -> bool:
    """Whether `arg` is an explicit filesystem path (vs. a bare uri).

    Only the unambiguous path forms count — leading `/`, `~`, `./`, `../`, or a
    lone `.`/`..`. A bare relative string (`api/v2`) is treated as a uri.
    """
    return arg.startswith(_PATH_PREFIXES) or arg in (".", "..")


def resolve_to_uri(arg: str, vault_uri: str, vault_path: Path, cwd: Path) -> str:
    """Resolve a scope argument (a uri OR a filesystem path) to a uri.

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
        f"{arg!r} is neither a uri in vault {vault_uri!r} nor a file/dir "
        f"inside it. Copy a uri from `ki outline`/`ki search`, or pass a path "
        f"under the vault."
    )
