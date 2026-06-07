"""Resolve which Neo4j profile a command should use.

Precedence (highest first):
  1. An explicit ``--profile`` flag — wins silently, even over a vault's
     own binding. The override escape hatch.
  2. The bound profile recorded in the vault's ``.ki/vault.yaml`` (the vault
     we're standing in, discovered by walking up from ``start_dir``). This is
     how each vault owns its profile — no global default needed.
  3. The config's ``default_profile`` / ``KI_PROFILE`` / sole profile, for
     commands run with no vault context (e.g. ``ki configure``).

This is the single source of truth for "which database does this command
talk to." Commands should call it instead of ``cfg.get_profile(flag)``
directly so the vault binding is always honored.
"""

from __future__ import annotations

from pathlib import Path

from .config import Config, Profile
from .vault import find_vault_root, read_vault_profile


class BoundProfileMissing(KeyError):
    """The vault names a profile that isn't in config (renamed / cloned machine).

    Carries the vault root and bound name so the caller can tell the user to
    re-bind (``ki use <profile>``) or re-create the profile.
    """

    def __init__(self, vault_root: Path, bound: str) -> None:
        super().__init__(
            f"vault at {vault_root} is bound to profile {bound!r}, which is not "
            f"in your config. Re-bind it with `ki use <profile>`, or re-create "
            f"the {bound!r} profile with `ki configure --profile {bound}`."
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
    if profile_flag:
        return cfg.get_profile(profile_flag)

    root = find_vault_root(start_dir or Path.cwd())
    if root is not None:
        bound = read_vault_profile(root)
        if bound:
            if bound not in cfg.profiles:
                raise BoundProfileMissing(root, bound)
            return cfg.profiles[bound]

    return cfg.get_profile(None)
