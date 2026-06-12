"""Theme producer (standalone) — Leiden over a vault's doc-level wikilink graph.

Prototype for `ki theme`; see docs/theme-format.md (output) and
docs/theme-queries.md (pipeline). Not wired into the CLI yet.

Usage (from a vault directory, or pass a path):
    uv run --with graphdatascience python scripts/theme_producer.py [vault-path]
        [--gamma 1.0] [--min-docs 3] [--json]

Resolves the vault uri + profile from <vault>/.ki/vault.yaml and credentials
from ~/.config/ki/config.yaml — same resolution ki itself uses.

Requires the GDS plugin on the profile's Neo4j. For the local Podman setup,
recreate the container with NEO4J_PLUGINS='["apoc","genai","graph-data-science"]'
(data volume persists across recreate).
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

PAIR_MATCH = """
MATCH (src)-[l:LINKS_TO]->(tgt)
WHERE src.uri STARTS WITH $vaultPrefix AND tgt.uri STARTS WITH $vaultPrefix
MATCH (s:Document {uri: split(src.uri, '#')[0]})
MATCH (t:Document {uri: split(tgt.uri, '#')[0]})
WHERE s.sourceType = 'LOCAL_FILE' AND t.sourceType = 'LOCAL_FILE' AND s <> t
"""

# Projection variant: target side also admits glue nodes (co-citation signal).
PROJECTION_MATCH = """
MATCH (src)-[l:LINKS_TO]->(tgt)
WHERE src.uri STARTS WITH $vaultPrefix AND tgt.uri STARTS WITH $vaultPrefix
MATCH (s:Document {uri: split(src.uri, '#')[0]})
MATCH (t:Document {uri: split(tgt.uri, '#')[0]})
WHERE s.sourceType = 'LOCAL_FILE'
  AND t.sourceType IN ['LOCAL_FILE', 'LOCAL_STUB', 'WIKILINK_UNRESOLVED']
  AND s <> t
"""

COHESION_COND_TIGHT, COHESION_COND_LOOSE = 0.2, 0.5
COHESION_WORD = {"tight": "tightly", "moderate": "moderately", "loose": "loosely"}


def cohesion(conductance: float) -> str:
    if conductance <= COHESION_COND_TIGHT:
        return "tight"
    if conductance >= COHESION_COND_LOOSE:
        return "loose"
    return "moderate"


def resolve_connection(vault_path: Path) -> tuple[str, str, str, str]:
    """Return (vault_uri, neo4j_uri, user, password) from ki's config files."""
    marker = vault_path / ".ki" / "vault.yaml"
    if not marker.exists():
        sys.exit(f"error: {vault_path} is not a ki vault (no .ki/vault.yaml)")
    vault = yaml.safe_load(marker.read_text())
    config_path = Path.home() / ".config" / "ki" / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    profile_name = vault["profile"]
    profile = config["profiles"].get(profile_name)
    if profile is None:
        sys.exit(f"error: profile '{profile_name}' (bound in {marker}) not in {config_path}")
    return vault["uri"], profile["uri"], profile["user"], profile["password"]


def compute(gds, vault_uri: str, gamma: float, min_theme_doc_count: int) -> dict:
    vault_prefix = vault_uri.rstrip("/") + "/"
    graph_name = f"ki-theme-{vault_uri.replace('/', '-')}"

    G, _ = gds.graph.cypher.project(
        PROJECTION_MATCH
        + """
        WITH s, t, count(*) AS weight
        RETURN gds.graph.project(
          $graph_name, s, t,
          { relationshipProperties: { weight: weight } },
          { undirectedRelationshipTypes: ['*'] }
        )
        """,
        graph_name=graph_name,
        vaultPrefix=vault_prefix,
    )

    try:
        gds.v2.leiden.mutate(
            G,
            mutate_property="themeId",
            relationship_weight_property="weight",
            gamma=gamma,
            random_seed=42,
            concurrency=1,
        )
        cond_df = gds.v2.conductance.stream(
            G, community_property="themeId", relationship_weight_property="weight"
        )
        coh = {r.community: cohesion(r.conductance) for r in cond_df.itertuples()}

        # Clear stale themeIds from prior runs (write only touches projected nodes)
        gds.run_cypher(
            """
            MATCH (d:Document)
            WHERE d.uri STARTS WITH $vaultPrefix AND d.themeId IS NOT NULL
            REMOVE d.themeId
            """,
            params={"vaultPrefix": vault_prefix},
        )
        gds.v2.graph.node_properties.write(G, ["themeId"])
    finally:
        gds.v2.graph.drop(G)

    # Fold sub-floor themes (member-doc count, not projected-node count) into ungrouped
    gds.run_cypher(
        """
        MATCH (m:Document {sourceType: 'LOCAL_FILE'})
        WHERE m.uri STARTS WITH $vaultPrefix AND m.themeId IS NOT NULL
        WITH m.themeId AS theme, count(m) AS docCount
        WHERE docCount < $minThemeDocCount
        WITH collect(theme) AS smallThemes
        MATCH (d:Document)
        WHERE d.uri STARTS WITH $vaultPrefix AND d.themeId IN smallThemes
        REMOVE d.themeId
        """,
        params={"vaultPrefix": vault_prefix, "minThemeDocCount": min_theme_doc_count},
    )

    members = gds.run_cypher(
        PAIR_MATCH
        + """
        AND s.themeId = t.themeId
        UNWIND [s, t] AS d
        WITH d.themeId AS theme, d, count(*) AS withinThemeLinks
        RETURN theme, d.uri AS uri, d.displayName AS displayName, withinThemeLinks
        ORDER BY theme, withinThemeLinks DESC, uri
        """,
        params={"vaultPrefix": vault_prefix},
    )
    targets = gds.run_cypher(
        """
        MATCH (src)-[l:LINKS_TO {wikilink: true}]->(tgt)
        WHERE src.uri STARTS WITH $vaultPrefix
        MATCH (s:Document {uri: split(src.uri, '#')[0]})
        WHERE s.sourceType = 'LOCAL_FILE' AND s.themeId IS NOT NULL
          AND split(tgt.uri, '#')[0] <> s.uri
        WITH s.themeId AS theme, tgt, count(DISTINCT s) AS linkingDocs
        ORDER BY theme, linkingDocs DESC, tgt.uri
        WITH theme,
             collect({uri: tgt.uri, displayName: tgt.displayName, docs: linkingDocs})[..5]
               AS targets
        RETURN theme, targets
        """,
        params={"vaultPrefix": vault_prefix},
    )
    crossovers = gds.run_cypher(
        PAIR_MATCH.replace("AND s <> t", "")
        + """
        AND s.themeId IS NOT NULL AND t.themeId IS NOT NULL AND s.themeId <> t.themeId
        WITH s.themeId AS theme, t.themeId AS otherTheme, s, count(*) AS crossLinks
        ORDER BY theme, otherTheme, crossLinks DESC, s.uri
        WITH theme, otherTheme, collect({uri: s.uri, displayName: s.displayName})[0] AS via
        RETURN theme, otherTheme, via
        """,
        params={"vaultPrefix": vault_prefix},
    )
    header = gds.run_cypher(
        """
        MATCH (d:Document {sourceType: 'LOCAL_FILE'})
        WHERE d.uri STARTS WITH $vaultPrefix
        RETURN count(d) AS totalDocs, count(d.themeId) AS groupedDocs
        """,
        params={"vaultPrefix": vault_prefix},
    ).iloc[0]

    rows = []
    for r in members.itertuples():
        rows.append({"cluster_key": str(r.theme), "kind": "member", "uri": r.uri,
                     "displayName": r.displayName, "count": int(r.withinThemeLinks),
                     "other_cluster_key": None,
                     "cohesion": coh.get(r.theme, "loose"), "exemplar_pos": None})
    for r in targets.itertuples():
        for t in r.targets:
            rows.append({"cluster_key": str(r.theme), "kind": "link_target",
                         "uri": t["uri"], "displayName": t["displayName"],
                         "count": int(t["docs"]), "other_cluster_key": None,
                         "cohesion": None, "exemplar_pos": None})
    for r in crossovers.itertuples():
        rows.append({"cluster_key": str(r.theme), "kind": "crossover",
                     "uri": r.via["uri"], "displayName": r.via["displayName"],
                     "count": None, "other_cluster_key": str(r.otherTheme),
                     "cohesion": None, "exemplar_pos": None})

    return {
        "method": "links",
        "total_docs": int(header.totalDocs),
        "grouped_docs": int(header.groupedDocs),
        "rows": rows,
    }


def render(result: dict, vault_uri: str, per_theme: int = 3) -> str:
    """Theme blocks per docs/theme-format.md."""
    themes: dict = {}
    for r in result["rows"]:
        t = themes.setdefault(r["cluster_key"],
                              {"members": [], "targets": [], "cross": [], "cohesion": "loose"})
        if r["kind"] == "member":
            t["members"].append(r)
            t["cohesion"] = r["cohesion"]
        elif r["kind"] == "link_target":
            t["targets"].append(r)
        else:
            t["cross"].append(r)

    order = sorted(themes.items(), key=lambda kv: (-len(kv[1]["members"]), kv[1]["members"][0]["uri"]))
    rank = {key: i + 1 for i, (key, _) in enumerate(order)}
    total, grouped = result["total_docs"], result["grouped_docs"]
    out = [f"THEMES  {vault_uri}   {total} docs · {grouped} grouped into "
           f"{len(order)} themes by wikilinks · {total - grouped} ungrouped"]
    for key, t in order:
        n = len(t["members"])
        out.append("")
        out.append(f"T{rank[key]}  {n} docs ({round(100 * n / total)}%) · "
                   f"{COHESION_WORD[t['cohesion']]} interlinked")
        binds = " · ".join(f"[[{x['displayName']}]] in {x['count']} docs"
                           for x in t["targets"][:3]) or "(none)"
        out.append(f"    top wikilink targets   {binds}")
        label = "    most-linked docs       "
        for m in t["members"][:per_theme]:
            out.append(f"{label}{m['displayName']:<40} {m['uri']}")
            label = " " * len(label)
        if n > per_theme:
            out.append(f"{label}(+{n - per_theme} more docs)")
        for c in sorted(t["cross"], key=lambda c: rank.get(c["other_cluster_key"], 99)):
            out.append(f"    links into T{rank.get(c['other_cluster_key'], '?')} via    "
                       f"{c['displayName']:<37} {c['uri']}")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("vault_path", nargs="?", default=".", help="vault directory (default: cwd)")
    ap.add_argument("--gamma", type=float, default=1.0,
                    help="Leiden resolution; >1 → more, smaller themes (default 1.0)")
    ap.add_argument("--min-docs", type=int, default=3,
                    help="themes with fewer member docs fold into ungrouped (default 3)")
    ap.add_argument("--json", action="store_true", help="emit wire records instead of the rendered view")
    args = ap.parse_args()

    vault_uri, neo4j_uri, user, password = resolve_connection(Path(args.vault_path).resolve())

    try:
        from graphdatascience import GraphDataScience
    except ImportError:
        sys.exit("error: graphdatascience not installed — run via:\n"
                 "  uv run --with graphdatascience python scripts/theme_producer.py")

    gds = GraphDataScience(neo4j_uri, auth=(user, password))
    try:
        gds.run_cypher("RETURN gds.version()")
    except Exception:
        sys.exit("error: GDS plugin not available on this profile's Neo4j.\n"
                 "For the local Podman setup, recreate the container with\n"
                 "NEO4J_PLUGINS='[\"apoc\",\"genai\",\"graph-data-science\"]' "
                 "(the data volume survives the recreate).")

    result = compute(gds, vault_uri, args.gamma, args.min_docs)
    print(json.dumps(result, indent=2) if args.json else render(result, vault_uri))


if __name__ == "__main__":
    main()
