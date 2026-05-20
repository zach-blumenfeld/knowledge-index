"""Integration tests for `ki rm`."""

from __future__ import annotations

import pytest

from ki.commands.rm import cmd_rm
from ki.config import Config, Profile, save_config
from ki.ingest.pipeline import IngestOptions, ingest_vault
from ki.neo4j_client import driver_for
from ki.vault import read_vault_uri

pytestmark = pytest.mark.integration


@pytest.fixture
def indexed_vault(vault_dir, neo4j_profile, cleanup_vault, monkeypatch, tmp_path):
    """Index the vault and write a Config so the rm command can resolve a profile."""
    res = ingest_vault(vault_dir, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)

    # cmd_rm reads config from disk — set up an isolated config that points at our test Neo4j.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cfg = Config()
    cfg.add_profile(Profile(
        name="default", uri=neo4j_profile.uri,
        user=neo4j_profile.user, password=neo4j_profile.password,
    ))
    save_config(cfg)
    return res.vault_uri


def _vault_doc_count(profile, vault_uri):
    with driver_for(profile) as driver:
        with driver.session() as session:
            row = session.run(
                "MATCH (v:Vault {uri: $u})-[:HAS*]->(d:Document) RETURN count(DISTINCT d) AS n",
                u=vault_uri,
            ).single()
            return row["n"] if row else 0


def test_rm_single_doc(indexed_vault, vault_dir, neo4j_profile):
    before = _vault_doc_count(neo4j_profile, indexed_vault)
    target = vault_dir / "concepts" / "duplicate-headings.md"
    assert target.exists(), f"fixture missing expected file: {target}"
    rc = cmd_rm(
        str(target), profile=None, vault_flag=False,
        dry_run=False, yes=True, keep_marker=False,
    )
    assert rc == 0
    after = _vault_doc_count(neo4j_profile, indexed_vault)
    assert after == before - 1


def test_rm_subtree_with_yes(indexed_vault, vault_dir, neo4j_profile):
    before = _vault_doc_count(neo4j_profile, indexed_vault)
    target = vault_dir / "inbox"
    assert target.is_dir(), f"fixture missing expected subdir: {target}"
    expected_removed = len(list(target.rglob("*.md")))
    rc = cmd_rm(
        str(target), profile=None, vault_flag=False,
        dry_run=False, yes=True, keep_marker=False,
    )
    assert rc == 0
    after = _vault_doc_count(neo4j_profile, indexed_vault)
    assert after == before - expected_removed


def test_rm_dry_run_writes_nothing(indexed_vault, vault_dir, neo4j_profile):
    before = _vault_doc_count(neo4j_profile, indexed_vault)
    target = vault_dir / "concepts" / "duplicate-headings.md"
    rc = cmd_rm(
        str(target), profile=None, vault_flag=False,
        dry_run=True, yes=False, keep_marker=False,
    )
    assert rc == 0
    after = _vault_doc_count(neo4j_profile, indexed_vault)
    assert after == before


def test_rm_vault_yes_removes_everything_and_marker(indexed_vault, vault_dir, neo4j_profile):
    assert read_vault_uri(vault_dir) is not None
    rc = cmd_rm(
        str(vault_dir), profile=None, vault_flag=True,
        dry_run=False, yes=True, keep_marker=False,
    )
    assert rc == 0
    assert _vault_doc_count(neo4j_profile, indexed_vault) == 0
    assert read_vault_uri(vault_dir) is None  # marker gone


def test_rm_vault_keep_marker(indexed_vault, vault_dir, neo4j_profile):
    rc = cmd_rm(
        str(vault_dir), profile=None, vault_flag=True,
        dry_run=False, yes=True, keep_marker=True,
    )
    assert rc == 0
    assert read_vault_uri(vault_dir) is not None  # marker preserved


def test_rm_dry_run_vault_writes_nothing(indexed_vault, vault_dir, neo4j_profile):
    before = _vault_doc_count(neo4j_profile, indexed_vault)
    rc = cmd_rm(
        str(vault_dir), profile=None, vault_flag=True,
        dry_run=True, yes=True, keep_marker=False,
    )
    assert rc == 0
    assert _vault_doc_count(neo4j_profile, indexed_vault) == before
    assert read_vault_uri(vault_dir) is not None
