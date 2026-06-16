"""`ki rm <doc-or-folder>` — remove one document or folder (and its subtree)
from the index. Source files are untouched.

`ki rm` is the *incremental* counterpart to `ki drop` (whole vault): it removes
a **file-or-above** unit — a Document or a Folder — and everything under it.
A Vault target errors → use `ki drop`; a Section target errors → a section
isn't an on-disk object you can delete, so removing it from the index while its
text is still in the document would put the index out of sync with disk (edit
the document and re-index instead).

Scope resolution mirrors `ki search --under` (see `docs/scoping.md`):
  - **Local (default)** — no `--profile`. Profile + vault come from the
    resolution dir's `.ki/vault.yaml` (cwd, or `-C <dir>`). The target is a uri
    in that vault, or a filesystem path inside it.
  - **Remote (`--profile P`)** — operate on a vault in profile `P` by uri.
    There's no local vault to resolve a path against, so the target must be a
    uri.

Inbound `[[wikilinks]]` that pointed at the removed subtree are dropped — that
is correct: the target is gone, so those references are now genuinely dangling.
`ki rm` does **not** re-resolve the *referrers'* links (re-resolving links you
didn't write would invent edges and drift from disk); fix the `[[wikilinks]]`
in your source like any file move, then a full `ki index` reconciles them.

Flags:
  --profile P    remote profile (target must then be a uri)
  --dry-run      report what would be removed; make no changes
  --json         machine-readable result
  --chunk-size N rows per batched-remove transaction (default 1000; lower on OOM)
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..config import Config, Profile, find_config_path, load_config
from ..ingest.remove import DEFAULT_CHUNK_SIZE, count_subtree, remove_subtree
from ..neo4j_client import driver_for
from ..profile_resolve import resolve_profile
from ..scope import looks_like_path, resolve_to_uri
from ..search.queries import run_b13
from ..vault import find_vault_root, read_vault_uri

_REMOVABLE = ("Document", "Folder")


def _resolve_rm_target(
    cfg: Config,
    *,
    profile_flag: str | None,
    target: str,
    start_dir: Path | None,
) -> tuple[Profile, str, str]:
    """(profile, target_uri, banner) — same two modes as `ki search --under`."""
    search_dir = start_dir or Path.cwd()
    via_c = start_dir is not None

    # --- remote mode: --profile names the profile; target is a uri, verbatim ---
    if profile_flag:
        prof = resolve_profile(cfg, profile_flag, start_dir=search_dir)
        if looks_like_path(target):
            raise click.ClickException(
                f"{target!r} looks like a path, but with --profile there's no "
                f"local vault to resolve it against. Pass a uri "
                f"(e.g. my-vault/folder/doc.md)."
            )
        target_uri = target.rstrip("/")
        return prof, target_uri, f"ki rm: profile '{prof.name}' · '{target_uri}'"

    # --- local mode: vault from the resolution dir's .ki marker; profile via
    # the standard chain (binding → $KI_PROFILE → error), same as drop/index. ---
    root = find_vault_root(search_dir)
    if root is None:
        raise click.ClickException(
            "ki rm needs a vault. Run inside a vault directory, point at one "
            "with -C <dir>, or pass --profile <name> + a uri for a remote profile."
        )
    vault_uri = read_vault_uri(root)
    if vault_uri is None:
        raise click.ClickException(
            f"{root} has a .ki dir but no vault uri on record — re-index it "
            f"with `ki index .`."
        )
    prof = resolve_profile(cfg, None, start_dir=root)
    target_uri = resolve_to_uri(target, vault_uri, root, cwd=search_dir)
    tag = f"  (-C {root})" if via_c else "  (from .ki)"
    return prof, target_uri, f"ki rm: profile '{prof.name}' · '{target_uri}'{tag}"


def cmd_rm(
    target: str,
    *,
    profile: str | None = None,
    dry_run: bool = False,
    as_json: bool = False,
    directory: Path | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> int:
    cfg_path = find_config_path()
    if cfg_path is None:
        raise click.ClickException("no ki config found — run `ki configure` first")
    cfg = load_config(cfg_path)

    prof, target_uri, banner = _resolve_rm_target(
        cfg, profile_flag=profile, target=target, start_dir=directory
    )
    if not as_json:
        click.echo(banner, err=True)

    with driver_for(prof) as driver, driver.session() as session:
        node = run_b13(session, target_uri)
        if node is None:
            raise click.ClickException(
                f"nothing indexed at {target_uri!r} — check the uri "
                f"(`ki outline` / `ki search`) or the path."
            )
        label = node.get("label")
        if label == "Vault":
            raise click.ClickException(
                f"{target_uri!r} is a whole vault — use `ki drop` to remove a "
                f"vault, not `ki rm`."
            )
        if label == "Section":
            raise click.ClickException(
                f"{target_uri!r} is a section, not a file. `ki rm` removes files "
                f"and folders (things that exist on disk); a section can't be "
                f"deleted on its own. Edit the document to drop the section, then "
                f"re-index."
            )
        if label not in _REMOVABLE:
            raise click.ClickException(
                f"can't `ki rm` a {label or 'unknown'} node ({target_uri!r}); "
                f"it removes Documents and Folders only."
            )

        if dry_run:
            by_label = count_subtree(session, target_uri)
            total = sum(by_label.values())
            if as_json:
                click.echo(
                    json.dumps(
                        {
                            "dry_run": True,
                            "uri": target_uri,
                            "label": label,
                            "nodes": total,
                            "by_label": by_label,
                        }
                    )
                )
            else:
                detail = ", ".join(f"{k}: {v}" for k, v in sorted(by_label.items()))
                click.echo(
                    f"dry-run: would remove {label} {target_uri!r} — "
                    f"{total} node(s) ({detail}). Source files untouched."
                )
            return 0

        stats = remove_subtree(session, target_uri, chunk_size=chunk_size)

    if as_json:
        click.echo(json.dumps({"uri": target_uri, "label": label, **stats}))
    else:
        msg = (
            f"✓ removed {label} {target_uri!r} from the index "
            f"({stats['nodes_removed']} node(s)). Source files untouched."
        )
        if stats["orphans_removed"]:
            msg += (
                f"\n  garbage-collected {stats['orphans_removed']} orphaned "
                f"external link target(s)."
            )
        click.echo(msg)
    return 0
