"""Theme producer plumbing that doesn't need a database."""

from __future__ import annotations

import pytest

from ki.commands.theme import _gds_missing_message
from ki.config import Profile
from ki.themes.queries import (
    CROSSOVERS,
    MEMBERS,
    PAIR_MATCH,
    PROJECTION_MATCH,
    TOP_TARGETS,
    graph_name_for,
)

# ---- graph naming ---------------------------------------------------------


def test_graph_name_strips_uri_punctuation():
    """GDS graph names reject ':' and '/', which every vault URI contains."""
    name = graph_name_for("vault://abc-123")
    assert name == "ki-theme-vault---abc-123"
    assert all(c.isalnum() or c in "-_" for c in name)


def test_graph_name_is_deterministic():
    """Determinism is load-bearing: the name doubles as a concurrency mutex."""
    assert graph_name_for("v/x") == graph_name_for("v/x")


def test_graph_names_differ_per_vault():
    assert graph_name_for("vault-a") != graph_name_for("vault-b")


# ---- query shape ----------------------------------------------------------


def test_projection_admits_glue_nodes_but_pair_match_does_not():
    """Glue nodes carry the co-citation signal into clustering, but they are
    never members, so the renderer's doc-doc form must exclude them."""
    for kind in ("LOCAL_STUB", "WIKILINK_UNRESOLVED"):
        assert kind in PROJECTION_MATCH
        assert kind not in PAIR_MATCH


def test_url_links_are_never_projected():
    """A pasted URL is a hub that welds unrelated docs together."""
    assert "URL_LINK" not in PROJECTION_MATCH
    # The vault-prefix filter on both ends is what actually drops them.
    assert PROJECTION_MATCH.count("STARTS WITH $vaultPrefix") == 2


def test_renderer_queries_require_a_theme():
    for q in (MEMBERS, CROSSOVERS):
        assert "themeId IS NOT NULL" in q


def test_top_targets_ignores_singleton_citations():
    """A target only one doc cites is that doc's private reference, not
    something the theme converges on."""
    assert "linkingDocs > 1" in TOP_TARGETS


def test_top_targets_only_counts_wikilinks():
    assert "LINKS_TO {wikilink: true}" in TOP_TARGETS


def test_section_uris_collapse_to_owning_document():
    """split(uri,'#')[0] hits the uri uniqueness index — no HAS* walk."""
    for q in (PAIR_MATCH, PROJECTION_MATCH):
        assert "split(src.uri, '#')[0]" in q
        assert "split(tgt.uri, '#')[0]" in q


# ---- capability error -----------------------------------------------------


def _profile(uri: str) -> Profile:
    return Profile(name="p", uri=uri, user="neo4j", password="x")


def test_local_error_gives_podman_recreate_one_command_per_line():
    msg = _gds_missing_message(_profile("bolt://localhost:7687"))
    assert "podman stop" in msg
    assert "podman rm" in msg
    assert "podman run" in msg
    assert "graph-data-science" in msg
    # Reassure the user their index survives — otherwise "rm the container"
    # reads as "lose everything".
    assert "no re-index" in msg
    # One command per line; no && chains or backslash continuations.
    for line in msg.splitlines():
        assert "&&" not in line
        assert not line.rstrip().endswith("\\")


def test_local_error_flags_that_container_names_are_defaults():
    msg = _gds_missing_message(_profile("bolt://localhost:7691"))
    assert "named yours differently" in msg
    # Non-canonical port must be preserved in the suggested run command.
    assert "7691:7687" in msg


def test_aura_error_explains_the_plugin_is_create_time_only():
    msg = _gds_missing_message(_profile("neo4j+s://abc123.databases.neo4j.io"))
    assert "--graph-analytics-plugin" in msg
    assert "created" in msg
    assert "podman" not in msg


@pytest.mark.parametrize("uri", ["bolt://neo4j.example.com:7687", "neo4j://10.0.0.5"])
def test_other_hosts_get_a_generic_message(uri):
    msg = _gds_missing_message(_profile(uri))
    assert "Graph Data Science" in msg
    assert "podman" not in msg
    assert "aura" not in msg.lower()
