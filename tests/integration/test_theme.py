"""End-to-end tests for the `ki theme` producer against a real Neo4j + GDS.

Skips (rather than fails) when the target Neo4j has no Graph Data Science
plugin, so the rest of the integration suite still runs against a stock
container. To get one, see skills/knowledge-base/references/neo4j-podman.md —
`graph-data-science` ships in ki's default plugin list.
"""

from __future__ import annotations

import pytest

from ki.ingest.pipeline import IngestOptions, ingest_vault
from ki.neo4j_client import driver_for
from ki.themes.producer import GdsUnavailable, check_gds, produce
from ki.themes.queries import graph_name_for

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def gds_session_check(neo4j_profile):
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        try:
            check_gds(session)
        except GdsUnavailable:
            pytest.skip(
                "Neo4j has no Graph Data Science plugin — recreate the "
                'container with NEO4J_PLUGINS=\'["apoc","genai",'
                '"graph-data-science"]\''
            )


@pytest.fixture
def clustered_vault(tmp_path, neo4j_profile, cleanup_vault, gds_session_check):
    """Two co-citation stars joined by one connector doc, plus three orphans.

    Shaped so the expected clustering is obvious by inspection, and so it
    exercises the design's central claim: a1..a4 **never link each other** —
    they only cite [[a-hub]]. If they end up in one theme, co-citation
    clustering works. Same for b1..b4 around [[b-hub]]. `connector` links
    across both, and the orphans link to nothing.

    Also drops a real attachment on disk, linked from two docs, so a
    `LOCAL_STUB` glue node exists to assert against.

    Note the naming: only the connector starts with "c", so a membership
    check can key off the first letter without the connector polluting it.
    """
    vault = tmp_path / "theme-vault"
    vault.mkdir()
    (vault / "attachment.pdf").write_bytes(b"%PDF-1.4 not a real pdf\n")

    (vault / "a-hub.md").write_text("# A hub\n\nthe thing the a-docs cite.\n")
    (vault / "b-hub.md").write_text("# B hub\n\nthe thing the b-docs cite.\n")
    for i in range(1, 5):
        extra = "\n\n[att](attachment.pdf)" if i <= 2 else ""
        (vault / f"a{i}.md").write_text(f"# A{i}\n\n[[a-hub]]{extra}\n")
    for i in range(1, 5):
        (vault / f"b{i}.md").write_text(f"# B{i}\n\n[[b-hub]]\n")
    (vault / "connector.md").write_text("# Connector\n\n[[a1]] [[a-hub]] [[b1]] [[b-hub]]\n")
    for i in range(1, 4):
        (vault / f"orphan{i}.md").write_text(f"# Orphan {i}\n\nno links here.\n")

    res = ingest_vault(vault, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)
    return res.vault_uri


def _produce(profile, vault_uri, **kw):
    with driver_for(profile) as driver, driver.session() as session:
        return produce(session, vault_uri, **kw)


# ---- the pipeline ---------------------------------------------------------


def _members_by_theme(result) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for m in result.rows:
        if m["kind"] == "member":
            out.setdefault(m["cluster_key"], set()).add(m["uri"].rsplit("/", 1)[-1])
    return out


def test_co_citation_groups_docs_that_never_link_each_other(
    clustered_vault, neo4j_profile
):
    """The design's central claim. a1..a4 share no edge — only a common target."""
    by_theme = _members_by_theme(_produce(neo4j_profile, clustered_vault))
    assert len(by_theme) == 2, by_theme
    for g in by_theme.values():
        letters = {n[0] for n in g if n[0] in ("a", "b")}
        assert len(letters) == 1, f"theme {g} mixes a- and b-docs"


def test_hub_lands_with_the_docs_that_cite_it(clustered_vault, neo4j_profile):
    by_theme = _members_by_theme(_produce(neo4j_profile, clustered_vault))
    for g in by_theme.values():
        if "a1.md" in g:
            assert "a-hub.md" in g
        if "b1.md" in g:
            assert "b-hub.md" in g


def test_header_reconciles_against_the_graph(clustered_vault, neo4j_profile):
    result = _produce(neo4j_profile, clustered_vault)
    # 2 hubs + 4 a-docs + 4 b-docs + connector + 3 orphans = 14.
    # The .pdf is a LOCAL_STUB, so it is not counted as a doc.
    assert result.total_docs == 14
    assert result.grouped_docs + result.ungrouped_docs == result.total_docs


def test_orphans_are_ungrouped(clustered_vault, neo4j_profile):
    result = _produce(neo4j_profile, clustered_vault)
    grouped = {r["uri"].rsplit("/", 1)[-1] for r in result.rows if r["kind"] == "member"}
    for i in range(1, 4):
        assert f"orphan{i}.md" not in grouped
    assert result.ungrouped_docs >= 3


def test_glue_nodes_never_become_members(clustered_vault, neo4j_profile):
    """attachment.pdf is a LOCAL_STUB: it participates in clustering (two docs
    citing the same attachment is evidence they belong together) but it is not
    a markdown document, so it must never be reported as a theme member."""
    result = _produce(neo4j_profile, clustered_vault)
    members = {r["uri"] for r in result.rows if r["kind"] == "member"}
    assert not any(u.endswith(".pdf") for u in members)


def test_shared_targets_surface_as_naming_evidence(clustered_vault, neo4j_profile):
    """The wikilink target the members converge on is the naming signal."""
    result = _produce(neo4j_profile, clustered_vault)
    targets = {r["displayName"] for r in result.rows if r["kind"] == "link_target"}
    assert {"a-hub.md", "b-hub.md"} & targets


def test_crossovers_point_at_the_other_theme(clustered_vault, neo4j_profile):
    result = _produce(neo4j_profile, clustered_vault)
    cross = [r for r in result.rows if r["kind"] == "crossover"]
    assert cross
    for c in cross:
        assert c["other_cluster_key"] != c["cluster_key"]


def test_cohesion_is_a_word_not_a_number(clustered_vault, neo4j_profile):
    result = _produce(neo4j_profile, clustered_vault)
    for r in result.rows:
        if r["kind"] == "member":
            assert r["cohesion"] in ("tight", "moderate", "loose")


# ---- determinism + state lifecycle ---------------------------------------


def test_runs_are_deterministic(clustered_vault, neo4j_profile):
    """Seeded Leiden at concurrency=1 must give identical assignments."""
    first = _produce(neo4j_profile, clustered_vault)
    second = _produce(neo4j_profile, clustered_vault)

    def signature(res):
        return sorted(
            (r["cluster_key"], r["uri"])
            for r in res.rows if r["kind"] == "member"
        )

    assert signature(first) == signature(second)


def test_projection_is_dropped_after_a_run(clustered_vault, neo4j_profile):
    """A leaked in-memory graph would block the next run."""
    _produce(neo4j_profile, clustered_vault)
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        rows = session.run(
            "CALL gds.graph.list() YIELD graphName RETURN graphName"
        ).data()
    assert graph_name_for(clustered_vault) not in {r["graphName"] for r in rows}


def test_theme_ids_are_written_to_the_graph(clustered_vault, neo4j_profile):
    _produce(neo4j_profile, clustered_vault)
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        n = session.run(
            "MATCH (d:Document {sourceType:'LOCAL_FILE'}) "
            "WHERE d.uri STARTS WITH $p AND d.themeId IS NOT NULL "
            "RETURN count(d) AS n",
            p=clustered_vault.rstrip("/") + "/",
        ).single()["n"]
    assert n >= 8


def test_stale_theme_ids_are_cleared(clustered_vault, neo4j_profile):
    """A doc that drops out of the projection must not keep an old themeId."""
    prefix = clustered_vault.rstrip("/") + "/"
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        session.run(
            "MATCH (d:Document) WHERE d.uri STARTS WITH $p "
            "SET d.themeId = 999999",
            p=prefix,
        ).consume()
        produce(session, clustered_vault)
        left = session.run(
            "MATCH (d:Document) WHERE d.uri STARTS WITH $p AND d.themeId = 999999 "
            "RETURN count(d) AS n",
            p=prefix,
        ).single()["n"]
    assert left == 0


def test_a_leaked_projection_self_heals(clustered_vault, neo4j_profile):
    """Simulates a run that crashed between project and drop."""
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        from ki.themes import queries as q

        session.run(
            q.PROJECT,
            graphName=graph_name_for(clustered_vault),
            vaultPrefix=clustered_vault.rstrip("/") + "/",
            exclude=[],
        ).consume()
        # Must not raise "graph already exists".
        result = produce(session, clustered_vault)
    assert result.grouped_docs > 0


# ---- knobs ----------------------------------------------------------------


def test_min_docs_folds_small_themes_into_ungrouped(clustered_vault, neo4j_profile):
    """The floor counts member docs, not projected nodes (glue doesn't count)."""
    high = _produce(neo4j_profile, clustered_vault, min_theme_docs=99)
    assert high.grouped_docs == 0
    assert high.ungrouped_docs == high.total_docs


def test_exclude_removes_a_doc_from_the_denominator(clustered_vault, neo4j_profile):
    base = _produce(neo4j_profile, clustered_vault)
    excl = _produce(neo4j_profile, clustered_vault, exclude=["connector.md"])
    assert excl.excluded_docs == 1
    assert excl.total_docs == base.total_docs - 1
    uris = {r["uri"] for r in excl.rows}
    assert not any(u.endswith("/connector.md") for u in uris)


def test_exclude_is_case_insensitive(clustered_vault, neo4j_profile):
    excl = _produce(neo4j_profile, clustered_vault, exclude=["CONNECTOR.MD"])
    assert excl.excluded_docs == 1


def test_granularity_is_plumbed_through(clustered_vault, neo4j_profile):
    """Higher granularity never yields fewer themes. Floor of 1 so the
    comparison measures Leiden's resolution, not the fold step."""
    coarse = _produce(neo4j_profile, clustered_vault, granularity=0.1, min_theme_docs=1)
    fine = _produce(neo4j_profile, clustered_vault, granularity=3.0, min_theme_docs=1)

    def theme_count(res):
        return len({r["cluster_key"] for r in res.rows if r["kind"] == "member"})

    assert theme_count(fine) >= theme_count(coarse)
