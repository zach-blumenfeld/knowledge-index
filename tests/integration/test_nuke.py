"""Integration tests for `ki nuke` — full graph reset.

See `docs/index_rm_behavior.md` *ki nuke* for the design.
"""

from __future__ import annotations

import pytest

from ki.commands.nuke import cmd_nuke
from ki.config import Config, Profile, save_config
from ki.ingest.pipeline import IngestOptions, ingest_vault
from ki.ingest.remove import list_all_vaults
from ki.neo4j_client import driver_for
from ki.vault import read_vault_uri

pytestmark = pytest.mark.integration


@pytest.fixture
def two_indexed_vaults(tmp_path, neo4j_profile, monkeypatch, cleanup_vault):
    """Set up two indexed vaults + a working config so cmd_nuke can resolve a profile."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    # Clear KI_PROFILE so a developer shell with `export KI_PROFILE=...` doesn't
    # override the temp config's default profile (see Config.get_profile).
    monkeypatch.delenv("KI_PROFILE", raising=False)
    cfg = Config()
    cfg.add_profile(Profile(
        name="default", uri=neo4j_profile.uri,
        user=neo4j_profile.user, password=neo4j_profile.password,
    ))
    save_config(cfg)

    a = tmp_path / "vault-a"
    a.mkdir()
    (a / "x.md").write_text("# X\n\nbody.\n")
    res_a = ingest_vault(a, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res_a.vault_uri)

    b = tmp_path / "vault-b"
    b.mkdir()
    (b / "y.md").write_text("# Y\n\nbody.\n")
    res_b = ingest_vault(b, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res_b.vault_uri)

    return [(a, res_a.vault_uri), (b, res_b.vault_uri)]


def test_nuke_dry_run_makes_no_changes(two_indexed_vaults, neo4j_profile):
    rc = cmd_nuke(profile=None, dry_run=True, yes=True, keep_marker=False)
    assert rc == 0
    # All vaults still present in the graph.
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        assert len(list_all_vaults(session)) == 2
    # Markers still on disk.
    for path, _uri in two_indexed_vaults:
        assert read_vault_uri(path) is not None


def test_nuke_removes_all_vaults_and_markers(two_indexed_vaults, neo4j_profile):
    rc = cmd_nuke(profile=None, dry_run=False, yes=True, keep_marker=False)
    assert rc == 0
    # Graph fully reset.
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        assert list_all_vaults(session) == []
        # No nodes at all (User nodes are also gone — full reset).
        row = session.run("MATCH (n) RETURN count(n) AS n").single()
        assert row["n"] == 0
    # All markers removed from disk.
    for path, _uri in two_indexed_vaults:
        assert read_vault_uri(path) is None


def test_nuke_keep_marker_preserves_yaml(two_indexed_vaults, neo4j_profile):
    rc = cmd_nuke(profile=None, dry_run=False, yes=True, keep_marker=True)
    assert rc == 0
    # Graph is reset.
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        assert list_all_vaults(session) == []
    # Markers preserved.
    for path, _uri in two_indexed_vaults:
        assert read_vault_uri(path) is not None


def test_nuke_drops_indexes_and_constraints(two_indexed_vaults, neo4j_profile):
    """After nuke, ki-owned schema is gone; the next `ki index` recreates it."""
    cmd_nuke(profile=None, dry_run=False, yes=True, keep_marker=True)
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        constraints = list(session.run("SHOW CONSTRAINTS YIELD name"))
        names = {r["name"] for r in constraints}
        for known in (
            "user_id_unique", "vault_uri_unique", "folder_uri_unique",
            "document_uri_unique", "section_uri_unique",
        ):
            assert known not in names

        indexes = list(session.run("SHOW INDEXES YIELD name"))
        index_names = {r["name"] for r in indexes}
        assert "content_search" not in index_names


def test_nuke_then_index_rebuilds_schema(two_indexed_vaults, neo4j_profile):
    """`ki index` after `ki nuke` recreates constraints + fulltext index via ensure_schema."""
    cmd_nuke(profile=None, dry_run=False, yes=True, keep_marker=True)

    # Re-ingest one of the vaults (markers preserved → same URIs).
    path, uri_before = two_indexed_vaults[0]
    res = ingest_vault(path, IngestOptions(profile=neo4j_profile, batch_size=64))
    # Same slug as before since the marker still has it.
    assert res.vault_uri == uri_before

    with driver_for(neo4j_profile) as driver, driver.session() as session:
        # Schema is back.
        constraints = list(session.run("SHOW CONSTRAINTS YIELD name"))
        names = {r["name"] for r in constraints}
        assert "vault_uri_unique" in names
        # Fulltext index is back.
        indexes = list(session.run("SHOW INDEXES YIELD name"))
        index_names = {r["name"] for r in indexes}
        assert "content_search" in index_names


def test_nuke_on_empty_graph_is_a_noop(tmp_path, neo4j_profile, monkeypatch):
    """`ki nuke` on a freshly-empty graph runs cleanly with --dry-run."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("KI_PROFILE", raising=False)
    cfg = Config()
    cfg.add_profile(Profile(
        name="default", uri=neo4j_profile.uri,
        user=neo4j_profile.user, password=neo4j_profile.password,
    ))
    save_config(cfg)
    # First wipe any leftover state.
    cmd_nuke(profile=None, dry_run=False, yes=True, keep_marker=True)
    # Now a dry-run on the empty graph.
    rc = cmd_nuke(profile=None, dry_run=True, yes=True, keep_marker=False)
    assert rc == 0
