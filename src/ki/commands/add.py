"""`ki add <path>` — incrementally (re)index one document or folder into an
EXISTING vault, without re-indexing the whole vault.

`ki add` is the write-surface composition `ki rm` + ingest-the-subtree (see
`src/ki/ingest/pipeline.py::ingest_subtree`): it clears the target subtree from
the index, then ingests just that subtree's markdown from disk. Use it after
you create or edit files under an already-indexed vault.

Local-only by nature — it reads files off disk, so there's no remote (`--profile
P` + uri) mode the way `ki rm` has. The vault is resolved by walking up from
`-C`/cwd; the target must be a path inside it. Re-indexing a *whole* vault is
`ki index`; `ki add` is for a single document or subfolder.

Inbound links are edge-restored across the re-ingest (still-valid `[[refs]]`
into the subtree survive, matching a full `ki index`; stale ones drop) — see
`ingest_subtree`.

Flags:
  --profile P    pick/override the vault's profile (else its .ki binding / $KI_PROFILE)
  --dry-run      list the markdown that would be (re)indexed; make no changes
  --json         machine-readable result
  --batch-size N rows per write transaction (default 1000)
  --chunk-size N rows per batched-remove transaction for the pre-ingest clear (default 1000)
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..config import find_config_path, load_config
from ..ingest.pipeline import IngestOptions, ingest_subtree
from ..profile_resolve import resolve_profile
from ..scope import resolve_to_uri
from ..vault import find_vault_root, iter_markdown_files, read_vault_uri


def cmd_add(
    target: str,
    *,
    profile: str | None = None,
    dry_run: bool = False,
    as_json: bool = False,
    directory: Path | None = None,
    batch_size: int = 1000,
    chunk_size: int = 1000,
) -> int:
    cfg_path = find_config_path()
    if cfg_path is None:
        raise click.ClickException("no ki config found — run `ki configure` first")
    cfg = load_config(cfg_path)

    search_dir = directory or Path.cwd()
    root = find_vault_root(search_dir)
    if root is None:
        raise click.ClickException(
            "ki add needs an existing vault. Run inside one (or point at it with "
            "-C <dir>); to create a vault, use `ki index <dir>`."
        )
    vault_uri = read_vault_uri(root)
    if vault_uri is None:
        raise click.ClickException(
            f"{root} has a .ki dir but no vault uri on record — run `ki index .`."
        )
    prof = resolve_profile(cfg, profile, start_dir=root)

    # Resolve the on-disk target (path only — a uri can't be reversed to a path).
    p = Path(target).expanduser()
    if not p.is_absolute():
        p = search_dir / p
    p = p.resolve()
    if not p.exists():
        raise click.ClickException(
            f"nothing at {target!r} on disk. `ki add` indexes a markdown file or "
            f"folder you've created/edited in the vault."
        )
    if not p.is_relative_to(root):
        raise click.ClickException(
            f"{target!r} is outside the vault at {root}. `ki add` only indexes "
            f"paths inside the vault."
        )
    rel = p.relative_to(root)
    if rel == Path("."):
        raise click.ClickException(
            "that's the whole vault — use `ki index` to (re)index an entire "
            "vault. `ki add` is for a single document or subfolder."
        )
    if p.is_file() and p.suffix.lower() != ".md":
        raise click.ClickException(
            f"{target!r} is not a markdown file. `ki add` indexes `.md` files (or "
            f"folders of them); other files are captured as link stubs when a "
            f"document references them."
        )

    target_uri = resolve_to_uri(str(p), vault_uri, root, cwd=search_dir)

    if dry_run:
        md_files = iter_markdown_files(p) if p.is_dir() else [p]
        if as_json:
            click.echo(
                json.dumps(
                    {
                        "dry_run": True,
                        "uri": target_uri,
                        "files": [str(f) for f in md_files],
                        "count": len(md_files),
                    }
                )
            )
        else:
            click.echo(
                f"ki add (dry-run): profile '{prof.name}' · '{target_uri}'", err=True
            )
            click.echo(
                f"would (re)index {len(md_files)} markdown file(s) under {rel}/ "
                f"(replacing any existing index entries for this subtree):"
                if p.is_dir()
                else f"would (re)index {rel} (replacing its existing index entry):"
            )
            for f in md_files:
                click.echo(f"  {f.relative_to(root)}")
        return 0

    if not as_json:
        click.echo(f"ki add: profile '{prof.name}' · '{target_uri}'", err=True)

    res = ingest_subtree(
        root,
        p,
        IngestOptions(profile=prof, batch_size=batch_size, chunk_size=chunk_size),
    )

    if as_json:
        click.echo(
            json.dumps(
                {
                    "uri": target_uri,
                    "vault_uri": res.vault_uri,
                    "docs_added": res.docs_added,
                    "sections_written": res.sections_written,
                    "links_written": res.links_written,
                    "folders_total": res.folders_total,
                    "docs_skipped_oversize": res.docs_skipped_oversize,
                }
            )
        )
    else:
        click.echo(
            f"✓ indexed {res.docs_added} document(s), {res.sections_written} "
            f"section(s), {res.links_written} link(s) under '{target_uri}'."
        )
        if res.docs_skipped_oversize:
            click.echo(
                f"  skipped {res.docs_skipped_oversize} oversize file(s) "
                f"(> {IngestOptions().max_file_size} bytes)."
            )
    return 0
