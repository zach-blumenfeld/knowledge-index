"""`ki outline` — render the containment tree of indexed vaults.

The CLI surface is `ki outline [<uri>]` (with `ki tree` kept as a permanent
alias for back-compat). This module powers both — the dispatcher lives in
`src/ki/cli.py`.

See `docs/commands/outline.md` for the rendered format spec, the wire record
schema, the sibling-ordering rules, and the `--full` description sub-line.
See `docs/data-model/retrieval-queries.md` (B.12 / B.12-links) for the underlying
queries.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from ..config import find_config_path, load_config
from ..neo4j_client import driver_for
from ..profile_resolve import resolve_profile
from ..search.queries import run_b12, run_b12_links
from ..vault import find_vault_root, read_vault_uri


@dataclass
class Row:
    """Wire record from B.12 / B.12-links — see docs/commands/outline.md."""

    depth: int
    inrel: str | None  # "HAS" | "LINKS_TO" | None
    label: str  # "Vault" | "Folder" | "Document" | "Section"
    name: str
    displayName: str
    uri: str
    parent_uri: str | None
    sort_pos: int | None
    description: str | None = None  # populated only for Vault rows under --full


INDENT_PER_DEPTH = 2
NAME_COL_CAP = 48
TYPE_LETTER = {"Vault": "V", "Folder": "F", "Document": "D", "Section": "S"}


def cmd_outline(
    *,
    profile: str | None,
    at: str | None,
    depth: int,
    full: bool,
    directory: Path | None = None,
) -> int:
    cfg_path = find_config_path()
    if cfg_path is None:
        raise click.ClickException("no ki config found — run `ki configure` first")
    cfg = load_config(cfg_path)

    explicit_uri = _parse_at(at)

    # Pick the render root, mirroring the scoping model (docs/scoping.md §3.2):
    #   - explicit <uri> / --at → render that subtree (local or remote).
    #   - --profile, no uri      → all vaults in that profile (multi-root).
    #   - else (local, no uri)   → the vault you're in (cwd, or -C <dir>).
    if explicit_uri is not None:
        root_uri: str | None = explicit_uri
    elif profile:
        root_uri = None
    else:
        vroot = find_vault_root(directory or Path.cwd())
        if vroot is None:
            raise click.ClickException(
                "ki outline needs a target: run inside a vault, point at one with "
                "-C <dir>, pass a <uri>, or use --profile <name> to render all "
                "vaults in a profile."
            )
        root_uri = read_vault_uri(vroot)

    prof = resolve_profile(cfg, profile, start_dir=directory)

    with driver_for(prof) as driver, driver.session() as session:
        rows = _collect_rows(session, root_uri, depth, full=full)

    if not rows:
        if root_uri is None:
            click.echo(
                "(no vaults indexed in this profile — run `ki index <path>` to create one)"
            )
        else:
            click.echo(
                f"(nothing indexed at `{root_uri}` — check `ki status`, "
                f"or `ki vault list` for the indexed vaults)"
            )
        return 0

    click.echo(_format_rows(rows, full=full))
    return 0


_LABEL_PREFIXES = ("Vault:", "Folder:", "Document:", "Section:")


def _parse_at(at: str | None) -> str | None:
    """Extract the URI from a positional URI / `--at` value.

    Accepts both `Label:uri` (the form documented in #17) and bare `uri`.
    The label prefix is documentation only — the URI is the load-bearing
    identifier and is what the query keys off.

    URI values may legitimately contain colons — external URL Documents
    (#37) use the URL itself as the URI (e.g. `https://beltagy.net/`), and
    `file://` URIs land here too. Only strip a prefix when it matches one
    of the four real node labels; otherwise treat the whole value as a
    bare URI.
    """
    if at is None:
        return None
    for prefix in _LABEL_PREFIXES:
        if at.startswith(prefix):
            uri = at[len(prefix):]
            return uri or None
    return at


def _collect_rows(
    session: Any,
    root_uri: str | None,
    depth: int,
    *,
    full: bool,
) -> list[Row]:
    raw_hier = run_b12(session, root_uri, depth=depth)
    hier_rows = [_row_from_b12(r) for r in raw_hier]

    ds_uris = [r.uri for r in hier_rows if r.label in ("Document", "Section")]
    raw_links = run_b12_links(session, ds_uris)
    depth_by_uri = {r.uri: r.depth for r in hier_rows}
    link_rows = [
        Row(
            depth=depth_by_uri[lr["parent_uri"]] + 1,
            inrel="LINKS_TO",
            label=lr["label"],
            name=lr["name"],
            displayName=lr["displayName"],
            uri=lr["uri"],
            parent_uri=lr["parent_uri"],
            sort_pos=None,
        )
        for lr in raw_links
        if lr["parent_uri"] in depth_by_uri
    ]

    all_rows = hier_rows + link_rows

    if full:
        vault_uris = [r.uri for r in all_rows if r.label == "Vault"]
        if vault_uris:
            descs = _fetch_vault_descriptions(session, vault_uris)
            for r in all_rows:
                if r.label == "Vault":
                    r.description = descs.get(r.uri)

    by_parent = _group_and_sort(all_rows)
    return _dfs_emit(by_parent)


def _row_from_b12(r: dict[str, Any]) -> Row:
    return Row(
        depth=r["depth"],
        inrel=r["inrel"],
        label=r["label"],
        name=r["name"],
        displayName=r["displayName"],
        uri=r["uri"],
        parent_uri=r["parent_uri"],
        sort_pos=r["sort_pos"],
    )


def _fetch_vault_descriptions(session: Any, uris: list[str]) -> dict[str, str | None]:
    res = session.run(
        "UNWIND $uris AS uri "
        "MATCH (v:Vault {uri: uri}) "
        "RETURN v.uri AS uri, v.description AS description",
        parameters={"uris": list(uris)},
    )
    return {r["uri"]: r["description"] for r in res}


def _group_and_sort(rows: list[Row]) -> dict[str | None, list[Row]]:
    by_parent: dict[str | None, list[Row]] = {}
    for r in rows:
        by_parent.setdefault(r.parent_uri, []).append(r)

    for parent_uri, kids in by_parent.items():
        if parent_uri is None:
            kids.sort(key=lambda k: k.name or "")
            continue
        has_others = [k for k in kids if k.inrel == "HAS" and k.label != "Section"]
        has_sections = [k for k in kids if k.inrel == "HAS" and k.label == "Section"]
        link_kids = [k for k in kids if k.inrel == "LINKS_TO"]
        has_others.sort(key=lambda k: k.name or "")
        has_sections.sort(
            key=lambda k: (k.sort_pos if k.sort_pos is not None else 0)
        )
        link_kids.sort(key=lambda k: k.uri or "")
        by_parent[parent_uri] = has_others + has_sections + link_kids
    return by_parent


def _dfs_emit(by_parent: dict[str | None, list[Row]]) -> list[Row]:
    output: list[Row] = []
    visited: set[str] = set()

    def emit(node_uri: str) -> None:
        # Guard against LINKS_TO cycles: a section can link back to an
        # ancestor (or any already-emitted node), which would otherwise
        # recurse forever. HAS is a single-parent tree and doesn't cycle,
        # but the merged HAS+LINKS_TO `by_parent` map can.
        if node_uri in visited:
            return
        visited.add(node_uri)
        for child in by_parent.get(node_uri, []):
            output.append(child)
            emit(child.uri)

    for root in by_parent.get(None, []):
        if root.uri in visited:
            continue
        output.append(root)
        emit(root.uri)
    return output


def _format_rows(rows: list[Row], *, full: bool) -> str:
    if not rows:
        return "(no results)"

    name_col = _compute_name_col(rows)

    lines: list[str] = []
    lines.append(
        "Key:  V Vault   F Folder   D Document   S Section   L Links-to"
    )
    lines.append("")
    lines.append(f"{'NAME':<{name_col}} T   URI")

    for r in rows:
        lines.append(_render_row(r, name_col))
        if full and r.label == "Vault" and r.description:
            desc = r.description.strip().replace("\n", " ")
            sub_indent = " " * ((r.depth + 1) * INDENT_PER_DEPTH)
            lines.append(f"{sub_indent}> {desc}")
    return "\n".join(lines)


def _compute_name_col(rows: list[Row]) -> int:
    max_left = max(len(_left_string(r)) for r in rows)
    # +3 = trailing " " + at least 2 dots before the type column.
    return min(NAME_COL_CAP, max_left + 3)


def _render_row(r: Row, name_col: int) -> str:
    left = _left_string(r)
    type_letter = "L" if r.inrel == "LINKS_TO" else TYPE_LETTER.get(r.label, "?")
    uri_display = _uri_display(r)

    if len(left) >= name_col:
        truncated = left[: name_col - 1] + "…"
        name_section = truncated.ljust(name_col)
    else:
        # left + 1 space + dots, filling to name_col chars.
        dots = "." * (name_col - len(left) - 1)
        name_section = f"{left} {dots}"
    return f"{name_section} {type_letter}   {uri_display}"


def _left_string(r: Row) -> str:
    indent = " " * (r.depth * INDENT_PER_DEPTH)
    if r.inrel == "LINKS_TO":
        return f"{indent}→ {_links_to_hint(r)}"
    if r.label == "Folder":
        return f"{indent}{r.name}/"
    if r.label == "Document":
        # `displayName` is the right column to render for every kind of
        # Document. For internal md docs `name == displayName == filename`
        # (per #28) so this is unchanged from the historical rendering. For
        # internal non-md stubs and external URLs (#37) `displayName` carries
        # the link-text label set on first ingest, which is far more useful
        # than the raw filename / URL — and the URI column already shows the
        # load-bearing identifier next to it.
        return f"{indent}{r.displayName or r.name}"
    if r.label == "Section":
        return f"{indent}{r.displayName or r.name}"
    # Vault.
    return f"{indent}{r.displayName or r.name}"


def _links_to_hint(r: Row) -> str:
    """Short human-readable hint for a LINKS_TO target.

    The target's `displayName` — heading text for Section targets, filename
    for Document targets, link-text label for #37 external / stub targets.
    The full target URI lives in the URI column on the same row, so the
    hint doesn't need to repeat any of it. Falls back to "links_to" when
    displayName is somehow unset.
    """
    return r.displayName or "links_to"


def _uri_display(r: Row) -> str:
    """URI column rendering — always the full URI.

    Every row shows the complete URI so the user / agent can copy-paste it
    directly into the next `ki outline <uri>` or `ki get <uri>`. We tried
    a `#fragment` shorthand for HAS-Section rows earlier; it saved visual
    space but forced the reader to walk up the indented hierarchy to
    reconstruct the full URI, which made the most-common follow-up (re-run
    `ki outline` rooted at the section) annoying.
    """
    return r.uri
