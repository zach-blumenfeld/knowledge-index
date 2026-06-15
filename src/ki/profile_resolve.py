"""Resolve which Neo4j profile a command should use.

Precedence (highest first) — see `docs/scoping.md` §4:
  1. An explicit ``--profile`` flag — wins over everything.
  2. The bound profile recorded in the vault's ``.ki/vault.yaml`` (the vault
     we're standing in, discovered by walking up from ``start_dir``). The
     normal path: each vault owns its profile.
  3. ``$KI_PROFILE`` if set — the last resort, only when there's no flag and
     no vault binding.

If none of those resolve, it's an **error**. `ki` has no default profile and
never auto-picks one (not even a sole profile) — *which database a command
talks to is never a silent guess.*

This is the single source of truth for "which database does this command
talk to." Commands should call it instead of ``cfg.get_profile(flag)``
directly so the vault binding is always honored.
"""

from __future__ import annotations

import os
from pathlib import Path

import click

from .config import PROFILE_ENV_VAR, Config, Profile
from .vault import find_vault_root, read_vault_profile


class BoundProfileMissing(click.ClickException):
    """The vault names a profile that isn't in config (renamed / cloned machine).

    Carries the vault root and bound name so the caller can tell the user to
    add the profile to config, or re-bind by re-indexing with one they have.
    A ``ClickException`` so it surfaces as a clean CLI error.
    """

    def __init__(self, vault_root: Path, bound: str) -> None:
        super().__init__(
            f"vault at {vault_root} is bound to profile {bound!r}, which is not "
            f"in your config. Add it with `ki configure`, or re-bind to an "
            f"existing profile by re-indexing: `ki index . --profile <p>`."
        )
        self.vault_root = vault_root
        self.bound = bound


def resolve_profile(
    cfg: Config,
    profile_flag: str | None,
    *,
    start_dir: Path | None = None,
) -> Profile:
    """Resolve the profile for a command. See module docstring for precedence."""
    # 1. explicit --profile
    if profile_flag:
        try:
            return cfg.get_profile(profile_flag)
        except KeyError as exc:
            raise click.ClickException(str(exc)) from exc

    # 2. the vault you're in (cwd, or -C <dir>)
    root = find_vault_root(start_dir or Path.cwd())
    if root is not None:
        bound = read_vault_profile(root)
        if bound:
            if bound not in cfg.profiles:
                raise BoundProfileMissing(root, bound)
            return cfg.profiles[bound]

    # 3. $KI_PROFILE — last resort
    env = os.environ.get(PROFILE_ENV_VAR)
    if env:
        try:
            return cfg.get_profile(env)
        except KeyError as exc:
            raise click.ClickException(
                f"$KI_PROFILE is set to {env!r}, but no such profile is in your "
                f"config. Fix or unset it, or pass --profile <name>."
            ) from exc

    # nothing resolved — no default exists, by design.
    raise click.ClickException(
        "no profile to use. Pass --profile <name>, run inside a vault "
        "(its .ki/vault.yaml binds a profile), or set KI_PROFILE."
    )
