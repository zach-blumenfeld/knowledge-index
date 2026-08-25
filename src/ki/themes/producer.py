"""Theme producer — runs the GDS pipeline and emits wire records.

Pipeline (docs/data-model/theme-queries.md): drop any stale projection →
project the doc-level link graph (docs + glue nodes, undirected, weighted)
→ Leiden mutate `themeId` → per-community conductance → clear stale / write
/ fold sub-floor → renderer queries → drop projection.

The output is the producer→renderer wire contract from
docs/commands/theme-format.md. Nothing here knows how a theme is *rendered*;
`ki.commands.theme` owns all formatting.

`themeId` is ki-owned index state, not user data (design principle #1) —
regenerated wholesale on every run, and safe for `ki index` / `ki drop` to
discard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from neo4j.exceptions import ClientError

from . import queries as q

# Conductance → cohesion word. Thresholds live here and are free to move;
# theme-format.md's contract is only the three phrases.
#
# Tuned against real vaults: the original 0.2/0.5 split produced zero
# `tight` themes across two independent vaults (content-research-wiki and
# content-research-kb), i.e. one third of the vocabulary never appeared.
# Real doc graphs leak more than the theory suggests — a genuinely coherent
# theme still cites its neighbours — so the bands are shifted up.
COHESION_TIGHT_MAX = 0.35
COHESION_LOOSE_MIN = 0.60

DEFAULT_GRANULARITY = 1.0
DEFAULT_MIN_THEME_DOCS = 3


class GdsUnavailable(RuntimeError):
    """The profile's Neo4j has no Graph Data Science plugin."""


@dataclass
class ThemeResult:
    """Producer output — the wire records plus the header scalars."""

    method: str
    vault_name: str
    total_docs: int
    grouped_docs: int
    excluded_docs: int
    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ungrouped_docs(self) -> int:
        return self.total_docs - self.grouped_docs


def cohesion(conductance: float) -> str:
    """Map a community's conductance to the cohesion word.

    Conductance is the fraction of the theme's link mass that leaves it, so
    low = tight. Chosen over edge density because it is not size-biased —
    `n·(n−1)/2` density makes every large theme read loose by construction —
    and because it is computed in-memory next to Leiden, costing no extra
    pass over the vault.
    """
    if conductance <= COHESION_TIGHT_MAX:
        return "tight"
    if conductance >= COHESION_LOOSE_MIN:
        return "loose"
    return "moderate"


def check_gds(session: Any) -> str:
    """Return the GDS version, or raise GdsUnavailable.

    Called before any projection work so a GDS-less profile fails fast with
    a capability error instead of a raw Cypher "unknown function".
    """
    try:
        rec = session.run(q.GDS_VERSION).single()
    except ClientError as exc:
        raise GdsUnavailable(str(exc)) from exc
    if rec is None:
        raise GdsUnavailable("gds.version() returned nothing")
    return str(rec["version"])


def produce(
    session: Any,
    vault_uri: str,
    *,
    granularity: float = DEFAULT_GRANULARITY,
    min_theme_docs: int = DEFAULT_MIN_THEME_DOCS,
    exclude: list[str] | None = None,
) -> ThemeResult:
    """Run the pipeline against one vault and return its wire records."""
    vault_prefix = vault_uri.rstrip("/") + "/"
    graph_name = q.graph_name_for(vault_uri)
    # Basenames and vault-relative paths both normalize to lowercase; the
    # leading "./" is stripped so `--exclude ./index.md` behaves.
    excl = sorted({e.lower().lstrip("./") for e in (exclude or []) if e.strip()})
    params = {"vaultPrefix": vault_prefix, "exclude": excl}

    check_gds(session)

    # A previous run that crashed between project and drop leaves the named
    # graph behind, which would fail this run at projection. failIfMissing
    # is false, so this is a no-op on the happy path and a self-heal
    # otherwise.
    session.run(q.DROP_GRAPH, graphName=graph_name).consume()
    session.run(q.PROJECT, graphName=graph_name, **params).consume()

    try:
        session.run(q.LEIDEN_MUTATE, graphName=graph_name, gamma=granularity).consume()
        coh = {
            r["community"]: cohesion(r["conductance"])
            for r in session.run(q.CONDUCTANCE, graphName=graph_name)
        }
        # Between the clear and the write the vault briefly has no themes —
        # the same "don't query mid-rebuild" caveat as `ki index`. A crash in
        # that window leaves docs *missing* themeIds, never wrong ones, and
        # the next run self-heals.
        session.run(q.CLEAR_STALE, **params).consume()
        session.run(q.NODE_PROPERTIES_WRITE, graphName=graph_name).consume()
    finally:
        session.run(q.DROP_GRAPH, graphName=graph_name).consume()

    session.run(
        q.FOLD_SMALL_THEMES, minThemeDocCount=min_theme_docs, **params
    ).consume()

    rows: list[dict[str, Any]] = []
    for r in session.run(q.MEMBERS, **params):
        rows.append(
            {
                "cluster_key": str(r["theme"]),
                "kind": "member",
                "uri": r["uri"],
                "displayName": r["displayName"],
                "count": int(r["withinThemeLinks"]),
                "other_cluster_key": None,
                "cohesion": coh.get(r["theme"], "loose"),
            }
        )
    for r in session.run(q.TOP_TARGETS, **params):
        for t in r["targets"]:
            rows.append(
                {
                    "cluster_key": str(r["theme"]),
                    "kind": "link_target",
                    "uri": t["uri"],
                    "displayName": t["displayName"],
                    "count": int(t["docs"]),
                    "other_cluster_key": None,
                    "cohesion": None,
                }
            )
    for r in session.run(q.CROSSOVERS, **params):
        rows.append(
            {
                "cluster_key": str(r["theme"]),
                "kind": "crossover",
                "uri": r["via"]["uri"],
                "displayName": r["via"]["displayName"],
                "count": None,
                "other_cluster_key": str(r["otherTheme"]),
                "cohesion": None,
            }
        )

    header = session.run(q.HEADER_SCALARS, **params).single()
    name_rec = session.run(q.VAULT_NAME, vaultUri=vault_uri.rstrip("/")).single()

    return ThemeResult(
        method="links",
        vault_name=str(name_rec["name"]) if name_rec else vault_uri,
        total_docs=int(header["totalDocs"]) if header else 0,
        grouped_docs=int(header["groupedDocs"]) if header else 0,
        excluded_docs=int(header["excludedDocs"]) if header else 0,
        rows=rows,
    )
