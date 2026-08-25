"""`ki theme` — group a vault's documents into themes by how they interlink.

Themes come from the wikilink graph and nothing else: two documents land in
the same theme because they link to each other, or because they converge on
the same targets. There is no folder-derived fallback — grouping by
directory hierarchy answers a different question, and presenting the two
identically would be dishonest (see issue #72).

`ki` never *names* a theme (no LLM at ingest or read — principle #17). Each
block ships the evidence an agent needs to name it: the shared wikilink
targets, the most-linked member docs, and full URIs to drill with.

See `docs/commands/theme-format.md` for the rendered format and the wire
record schema, and `docs/data-model/theme-queries.md` for the pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import click

from ..config import Profile, find_config_path, load_config
from ..neo4j_client import driver_for
from ..profile_resolve import resolve_profile
from ..themes.producer import GdsUnavailable, ThemeResult, produce
from ..vault import find_vault_root, read_vault_uri

# Row geometry, shared with outline-format.md so theme rows and outline rows
# line up when an agent reads both in one context.
NAME_COL_CAP = 48
# Label column: 4-space indent + the widest label ("top wikilink targets",
# 20 chars) + 3 spaces of gap = content starts at column 27.
LABEL_INDENT = 4
LABEL_WIDTH = 23

COHESION_WORD = {
    "tight": "tightly interlinked",
    "moderate": "moderately interlinked",
    "loose": "loosely interlinked",
}

DEFAULT_PER_THEME = 3
DEFAULT_TOP_TARGETS = 3


def cmd_theme(
    *,
    profile: str | None,
    granularity: float,
    min_docs: int,
    per_theme: int,
    top_k: int | None,
    exclude: tuple[str, ...],
    directory: Path | None = None,
) -> int:
    cfg_path = find_config_path()
    if cfg_path is None:
        raise click.ClickException("no ki config found — run `ki configure` first")
    cfg = load_config(cfg_path)

    start_dir = directory or Path.cwd()
    vroot = find_vault_root(start_dir)
    if vroot is None:
        raise click.ClickException(
            "ki theme needs a vault: run inside one, or point at it with "
            "-C <dir>. Themes are computed per vault, so there is no "
            "all-vaults mode — use `ki vault list` to see what's indexed."
        )
    vault_uri = read_vault_uri(vroot)

    prof = resolve_profile(cfg, profile, start_dir=directory)

    with driver_for(prof) as driver, driver.session() as session:
        try:
            result = produce(
                session,
                vault_uri,
                granularity=granularity,
                min_theme_docs=min_docs,
                exclude=list(exclude),
            )
        except GdsUnavailable:
            raise click.ClickException(_gds_missing_message(prof)) from None

    click.echo(render(result, per_theme=per_theme, top_k=top_k))
    return 0


# --- capability error -----------------------------------------------------


def _is_local(uri: str) -> bool:
    host = (urlparse(uri).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def _is_aura(uri: str) -> bool:
    host = (urlparse(uri).hostname or "").lower()
    return host.endswith("neo4j.io")


def _gds_missing_message(prof: Profile) -> str:
    """Explain the missing plugin in terms of the profile the user actually has.

    Deliberately does *not* offer to fix it: recreating a container is a
    destructive action, and a read-shaped command shouldn't initiate one
    (principle #4 — safe by default, dangerous by flag).
    """
    head = (
        "ki theme needs the Graph Data Science plugin, and this profile's "
        f"Neo4j doesn't have it (profile: {prof.name}, {prof.uri})."
    )

    if _is_local(prof.uri):
        from ..neo4j_podman import (
            CONTAINER_NAME,
            HEAP_MAX_SIZE,
            IMAGE,
            PAGECACHE_SIZE,
            PLUGINS_ENV,
            VOLUME_NAME,
        )

        port = urlparse(prof.uri).port or 7687
        browser = port - 7687 + 7474 if port != 7687 else 7474
        return (
            f"{head}\n\n"
            "Containers created before ki 0.6 didn't install it. Recreating "
            "the container adds it, and your indexed data lives in a named "
            "volume, so it survives — no re-index needed.\n\n"
            f"These use ki's default container and volume names ({CONTAINER_NAME} "
            f"/ {VOLUME_NAME}); if you named yours differently, substitute "
            "them (`podman ps` shows which container is on port "
            f"{port}, and `podman inspect <name>` shows its volume):\n\n"
            f"  podman stop {CONTAINER_NAME}\n"
            f"  podman rm {CONTAINER_NAME}\n"
            f"  podman run -d --name {CONTAINER_NAME} --restart unless-stopped "
            f"-p {browser}:7474 -p {port}:7687 -v {VOLUME_NAME}:/data "
            f"-e NEO4J_AUTH={prof.user}/<your-password> "
            f"-e 'NEO4J_PLUGINS={PLUGINS_ENV}' "
            f"-e NEO4J_server_memory_heap_max__size={HEAP_MAX_SIZE} "
            f"-e NEO4J_server_memory_pagecache_size={PAGECACHE_SIZE} "
            f"{IMAGE}\n\n"
            "The first start after this downloads the plugin and takes a minute."
        )

    if _is_aura(prof.uri):
        return (
            f"{head}\n\n"
            "On Aura the plugin can only be enabled when the instance is "
            "created — there is no way to add it to an existing one, and "
            "free instances can't have it at all. To use `ki theme` you'd "
            "need a new instance created with graph analytics enabled:\n\n"
            "  neo4j-cli aura instance create --type professional-db "
            "--graph-analytics-plugin --wait --rw\n\n"
            "then point a ki profile at it (`ki configure`) and re-index. "
            "Every other ki command works fine on this profile as-is."
        )

    return (
        f"{head}\n\n"
        "Install the Graph Data Science plugin on this Neo4j (it ships with "
        "Neo4j Enterprise and is available for Community), then re-run. "
        "Every other ki command works fine without it."
    )


# --- renderer -------------------------------------------------------------


def _theme_blocks(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    themes: dict[str, dict[str, Any]] = {}
    for r in rows:
        t = themes.setdefault(
            r["cluster_key"],
            {"members": [], "targets": [], "crossovers": [], "cohesion": "loose"},
        )
        if r["kind"] == "member":
            t["members"].append(r)
            t["cohesion"] = r["cohesion"] or "loose"
        elif r["kind"] == "link_target":
            t["targets"].append(r)
        elif r["kind"] == "crossover":
            t["crossovers"].append(r)
    return themes


def render(
    result: ThemeResult,
    *,
    per_theme: int = DEFAULT_PER_THEME,
    top_k: int | None = None,
) -> str:
    themes = _theme_blocks(result.rows)

    # Theme blocks sort by doc count descending; ties broken by smallest
    # member URI so the order is stable across runs. T<id> is assigned in
    # that order at render time, so blocks always read T1, T2, T3…
    order = sorted(
        themes.items(),
        key=lambda kv: (-len(kv[1]["members"]), min(m["uri"] for m in kv[1]["members"])),
    )
    tid = {key: i + 1 for i, (key, _) in enumerate(order)}

    shown = order if top_k is None else order[:top_k]
    hidden = order[len(shown):]
    shown_keys = {key for key, _ in shown}

    lines = [_header(result, len(order), shown, hidden)]

    # One name column across the whole output so rows line up between themes.
    name_col = _compute_name_col(shown, per_theme, shown_keys)

    for key, t in shown:
        lines.append("")
        lines.extend(_render_block(t, tid[key], tid, shown_keys, result, per_theme, name_col))

    if not order:
        lines.append("")
        lines.append(
            "(no themes — this vault's documents don't interlink enough to "
            "group. Check `ki outline` to confirm links were indexed.)"
        )
    return "\n".join(lines)


def _header(
    result: ThemeResult,
    theme_count: int,
    shown: list[Any],
    hidden: list[Any],
) -> str:
    parts = (
        f"THEMES  {result.vault_name}   {result.total_docs} docs · "
        f"{result.grouped_docs} grouped into {theme_count} themes by wikilinks · "
        f"{result.ungrouped_docs} ungrouped"
    )
    # Excluded docs leave the denominator entirely — the segment exists so
    # that removal is visible, never silent.
    if result.excluded_docs:
        parts += f" · {result.excluded_docs} excluded"
    # The top-k cut is display-only: hidden themes are real and stay counted
    # in `grouped`, so the header has to account for them.
    if hidden:
        hidden_docs = sum(len(t["members"]) for _, t in hidden)
        parts += (
            f" (showing top {len(shown)} — {len(hidden)} smaller themes "
            f"cover {hidden_docs} more docs)"
        )
    return parts


def _render_block(
    t: dict[str, Any],
    theme_no: int,
    tid: dict[str, int],
    shown_keys: set[str],
    result: ThemeResult,
    per_theme: int,
    name_col: int,
) -> list[str]:
    members = t["members"]
    n = len(members)
    pct = round(100 * n / result.total_docs) if result.total_docs else 0
    out = [
        f"T{theme_no}  {n} docs ({pct}%) · "
        f"{COHESION_WORD.get(t['cohesion'], COHESION_WORD['loose'])}"
    ]

    out.append(_label_line("top wikilink targets", _targets_text(t["targets"])))

    label = "most-linked docs"
    for m in members[:per_theme]:
        out.append(_label_line(label, _doc_row(m["displayName"], m["uri"], name_col)))
        label = ""
    if n > per_theme:
        # Membership is never silently truncated — rows + remainder always
        # reconcile to the theme line's doc count.
        out.append(_label_line("", f"(+{n - per_theme} more docs)"))

    # Crossovers into hidden themes are suppressed so no dangling T<j>
    # reference can appear. One line per connected theme, ascending T<j>.
    cross = [c for c in t["crossovers"] if c["other_cluster_key"] in shown_keys]
    for c in sorted(cross, key=lambda c: tid[c["other_cluster_key"]]):
        out.append(
            _label_line(
                f"links into T{tid[c['other_cluster_key']]} via",
                _doc_row(c["displayName"], c["uri"], name_col),
            )
        )
    return out


def _label_line(label: str, content: str) -> str:
    return " " * LABEL_INDENT + label.ljust(LABEL_WIDTH) + content


def _targets_text(targets: list[dict[str, Any]]) -> str:
    """`[[GraphRAG]] in 31 docs · [[Lucene]] in 12 · [[chunking]] in 9`.

    The unit rides on the first entry only — enough to define the number for
    the reader without repeating "docs" three times.

    The `[[…]]` rendering signals these are the *user's own* wikilink text,
    not something ki invented. `displayName` is the filename today, so the
    `.md` is stripped: the user wrote `[[aip-paper]]`, not `[[aip-paper.md]]`.
    """
    if not targets:
        return "(none shared by 2+ docs)"
    chunks = []
    for i, t in enumerate(targets[:DEFAULT_TOP_TARGETS]):
        name = t["displayName"] or t["uri"]
        if name.lower().endswith(".md"):
            name = name[:-3]
        unit = " docs" if i == 0 else ""
        chunks.append(f"[[{name}]] in {t['count']}{unit}")
    return " · ".join(chunks)


def _compute_name_col(
    shown: list[Any], per_theme: int, shown_keys: set[str]
) -> int:
    """Widest rendered doc name across the output, capped per outline-format."""
    widest = 0
    for _key, t in shown:
        for m in t["members"][:per_theme]:
            widest = max(widest, len(m["displayName"] or m["uri"]))
        for c in t["crossovers"]:
            if c["other_cluster_key"] in shown_keys:
                widest = max(widest, len(c["displayName"] or c["uri"]))
    # +3 = trailing " " + at least 2 dots before the type column.
    return min(NAME_COL_CAP, widest + 3)


def _doc_row(display_name: str | None, uri: str, name_col: int) -> str:
    """Outline row shape: name + dotted leader + type letter + full URI.

    `D` in v1 — themes are document-level. URIs are never truncated; the
    name side truncates at the cap with `…` (outline-format *Truncation*),
    because the URI is the load-bearing part an agent copies into the next
    `ki outline <uri>` / `ki get <uri>`.
    """
    left = display_name or uri
    if len(left) >= name_col:
        name_section = (left[: name_col - 1] + "…").ljust(name_col)
    else:
        dots = "." * (name_col - len(left) - 1)
        name_section = f"{left} {dots}"
    return f"{name_section} D   {uri}"
