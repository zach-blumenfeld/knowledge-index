"""`ki get <uri> [<uri> ...]` — fetch a node's metadata and content by URI.

Pairs with `ki outline` / `ki search`: those return URIs, this fetches what
the URI points to. Only `:Document` and `:Section` URIs are valid — text
retrieval is what this command does. `:Folder` and `:Vault` URIs error
with a hint pointing at `ki outline` / `ki vault list`.

`--type` controls how much content rides along on the metadata shell:
  path     → no content; just the shell (uri, name, displayName, path, ...)
  content  → node's stored `content` field (preamble + Rule 1 URI pointers)
  full     → reconstructed reading-order body via B.4 (Documents) or B.14
             (Sections). One Neo4j query, no client-side recursion.

See `docs/data-model/retrieval-queries.md` (B.4 / B.13 / B.14) for the queries.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from ..config import find_config_path, load_config
from ..neo4j_client import driver_for
from ..profile_resolve import resolve_profile
from ..search.queries import run_b4, run_b13, run_b14

VALID_TYPES = ("path", "content", "full")
TEXT_NODE_LABELS = ("Document", "Section")


def cmd_get(
    uris: tuple[str, ...],
    *,
    profile: str | None,
    get_type: str,
    as_json: bool,
    directory: Path | None = None,
) -> int:
    if get_type not in VALID_TYPES:
        raise click.ClickException(
            f"unknown --type {get_type!r}; expected one of {VALID_TYPES}"
        )
    if not uris:
        raise click.ClickException("ki get requires at least one URI")

    cfg_path = find_config_path()
    if cfg_path is None:
        raise click.ClickException("no ki config found — run `ki configure` first")
    cfg = load_config(cfg_path)
    prof = resolve_profile(cfg, profile, start_dir=directory)

    results: list[dict[str, Any]] = []
    errors: list[tuple[str, str]] = []  # (uri, message)

    with driver_for(prof) as driver, driver.session() as session:
        for uri in uris:
            row = run_b13(session, uri)
            if row is None:
                errors.append((uri, f"no node found for uri: {uri}"))
                continue

            label = row.get("label")
            if label not in TEXT_NODE_LABELS:
                errors.append((uri, _bad_label_message(label, uri)))
                continue

            shell = _shell_for_label(row)
            if get_type == "full":
                shell["content"] = _full_content(session, label, uri, row)
            elif get_type == "path":
                shell["content"] = None
            # else "content": leave shell["content"] as B.13 returned it.
            results.append(shell)

    return _render(results, errors, get_type=get_type, as_json=as_json)


def _bad_label_message(label: str | None, uri: str) -> str:
    if label == "Folder":
        return (
            f"ki get is for text retrieval but you passed a Folder ({uri}). "
            f"Use 'ki outline {uri}' to enumerate contents recursively under folder."
        )
    if label == "Vault":
        return (
            f"ki get is for text retrieval but you passed a Vault ({uri}). "
            f"Use 'ki vault list' to see vaults "
            f"or 'ki outline {uri}' to enumerate contents recursively under Vault."
        )
    return f"unsupported node label {label!r} for ki get (uri: {uri})"


def _shell_for_label(row: dict[str, Any]) -> dict[str, Any]:
    """Filter the union row from B.13 down to the props relevant for the label.

    The metadata "shell" `ki get` always returns. `--type` controls what
    ends up in the `content` field on top of this shell.
    """
    label = row.get("label")
    common = {
        "uri": row.get("uri"),
        "label": label,
        "name": row.get("name"),
        "displayName": row.get("displayName"),
        "path": row.get("path"),
        "aliases": row.get("aliases"),
        "content": row.get("content"),
    }
    if label == "Document":
        common.update(
            {
                "frontmatter": row.get("frontmatter"),
                "sourceType": row.get("sourceType"),
                "firstLoadedAt": row.get("firstLoadedAt"),
                "lastLoadedAt": row.get("lastLoadedAt"),
            }
        )
    elif label == "Section":
        common.update(
            {
                "headingLevel": row.get("headingLevel"),
            }
        )
    return common


def _full_content(
    session: Any, label: str, uri: str, shell_row: dict[str, Any]
) -> str:
    """Reconstruct reading-order body for `--type full`.

    Document → B.4 sections joined in reading order, prefixed by the
    document's own preamble (Document.content, which carries the
    pre-first-heading text per Rule 1).

    Section → B.14 subtree in NEXT_SECTION order.
    """
    if label == "Document":
        rows = run_b4(session, uri)
        return _format_sections(rows, preamble=shell_row.get("content"))
    if label == "Section":
        rows = run_b14(session, uri)
        return _format_sections(rows, preamble=None)
    return ""


def _format_sections(rows: list[dict[str, Any]], *, preamble: str | None) -> str:
    """Concatenate ordered section rows into a single markdown string.

    Each section emits `<#...> heading\\n\\n<content>` if it has a heading,
    or just the content if it doesn't. Preamble (doc-level text before the
    first heading) is prepended without any heading.
    """
    parts: list[str] = []
    if preamble and preamble.strip():
        parts.append(preamble.rstrip())
    for r in rows:
        level = r.get("heading_level") or 0
        heading = r.get("heading") or ""
        content = r.get("content") or ""
        hashes = "#" * level if level else ""
        if hashes and heading:
            block = f"{hashes} {heading}".rstrip()
            if content.strip():
                block = f"{block}\n\n{content.rstrip()}"
        else:
            block = content.rstrip()
        if block:
            parts.append(block)
    return "\n\n".join(parts)


def _render(
    results: list[dict[str, Any]],
    errors: list[tuple[str, str]],
    *,
    get_type: str,
    as_json: bool,
) -> int:
    """Emit results + errors. Returns exit code (0 success, 1 if any errors)."""
    if as_json:
        payload = {
            "type": get_type,
            "results": results,
            "errors": [{"uri": u, "message": m} for u, m in errors],
        }
        click.echo(json.dumps(payload, default=str, indent=2))
    else:
        _render_text(results, get_type=get_type)
        for _uri, msg in errors:
            print(f"error: {msg}", file=sys.stderr)
    return 0 if not errors else 1


def _render_text(results: list[dict[str, Any]], *, get_type: str) -> None:
    """Plain-text rendering: metadata header per result, then content block."""
    blocks: list[str] = []
    for r in results:
        blocks.append(_render_one(r, get_type=get_type))
    if blocks:
        click.echo("\n\n---\n\n".join(blocks))


def _render_one(row: dict[str, Any], *, get_type: str) -> str:
    label = row.get("label")
    lines: list[str] = []
    lines.append(str(row.get("uri") or ""))
    lines.append(f"  label: {label}")
    lines.append(f"  name: {row.get('name') or ''}")
    if row.get("displayName") and row.get("displayName") != row.get("name"):
        lines.append(f"  displayName: {row.get('displayName')}")
    if row.get("path"):
        lines.append(f"  path: {row.get('path')}")
    if row.get("aliases"):
        lines.append(f"  aliases: {row.get('aliases')}")
    if label == "Document":
        if row.get("sourceType"):
            lines.append(f"  sourceType: {row.get('sourceType')}")
        if row.get("frontmatter"):
            lines.append(f"  frontmatter: {row.get('frontmatter')}")
        if row.get("firstLoadedAt"):
            lines.append(f"  firstLoadedAt: {row.get('firstLoadedAt')}")
        if row.get("lastLoadedAt"):
            lines.append(f"  lastLoadedAt: {row.get('lastLoadedAt')}")
    elif label == "Section":
        if row.get("headingLevel") is not None:
            lines.append(f"  headingLevel: {row.get('headingLevel')}")

    content = row.get("content")
    if get_type == "path":
        lines.append("")
        lines.append(
            f"# --type path: no content emitted. "
            f"Read {row.get('path')!r} or rerun with --type content / --type full."
        )
    elif content:
        lines.append("")
        lines.append(content if isinstance(content, str) else str(content))
    return "\n".join(lines)
