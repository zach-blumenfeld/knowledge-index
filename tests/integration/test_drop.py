"""Integration tests for `ki drop` — vault-only model (see docs/data-model/index_rm_behavior.md)."""

from __future__ import annotations

import click
import pytest

from ki.commands.drop import cmd_drop
from ki.config import Config, Profile, save_config
from ki.ingest.pipeline import IngestOptions, ingest_vault
from ki.neo4j_client import driver_for
from ki.vault import read_vault_uri

pytestmark = pytest.mark.integration


@pytest.fixture
def indexed_vault(vault_dir, neo4j_profile, cleanup_vault, monkeypatch, tmp_path):
    """Index the vault and write a Config so the drop command can resolve a profile."""
    res = ingest_vault(vault_dir, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    # Drop-by-path resolves the profile via the target vault's binding; drop-by-slug
    # has no local dir, so it resolves via $KI_PROFILE (the last resort — there is
    # no default profile). Set it so the profile=None slug calls resolve.
    monkeypatch.setenv("KI_PROFILE", neo4j_profile.name)
    cfg = Config()
    cfg.add_profile(Profile(
        name=neo4j_profile.name, uri=neo4j_profile.uri,
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


# ---- Vault-only behavior: sub-vault targets error -------------------------


def test_drop_file_target_errors_with_helpful_message(indexed_vault, vault_dir, neo4j_profile):
    """Passing a file path is the most common "I meant document-level rm" mistake.

    Should error with the canonical sub-vault message that points the user at
    `ki index` (the only sync mechanism for sub-vault content).
    """
    target = vault_dir / "concepts" / "duplicate-headings.md"
    assert target.exists()
    before = _vault_doc_count(neo4j_profile, indexed_vault)
    with pytest.raises(click.ClickException) as exc_info:
        cmd_drop(
            str(target), profile=None,
            dry_run=False, yes=True, keep_marker=False,
        )
    msg = exc_info.value.message
    assert "file" in msg.lower()
    assert "ki drop only operates on whole vaults" in msg
    assert "ki index" in msg
    # Graph state unchanged.
    after = _vault_doc_count(neo4j_profile, indexed_vault)
    assert after == before


def test_drop_subdirectory_target_errors(indexed_vault, vault_dir, neo4j_profile):
    """Passing a subdirectory inside a vault errors — only vault roots are accepted."""
    target = vault_dir / "inbox"
    assert target.is_dir()
    before = _vault_doc_count(neo4j_profile, indexed_vault)
    with pytest.raises(click.ClickException) as exc_info:
        cmd_drop(
            str(target), profile=None,
            dry_run=False, yes=True, keep_marker=False,
        )
    msg = exc_info.value.message
    assert "not a vault root" in msg
    assert "ki drop only operates on whole vaults" in msg
    after = _vault_doc_count(neo4j_profile, indexed_vault)
    assert after == before


# ---- Vault-level removal (path + slug forms) ------------------------------


def test_drop_vault_path_removes_everything_and_marker(indexed_vault, vault_dir, neo4j_profile):
    assert read_vault_uri(vault_dir) is not None
    rc = cmd_drop(
        str(vault_dir), profile=None,
        dry_run=False, yes=True, keep_marker=False,
    )
    assert rc == 0
    assert _vault_doc_count(neo4j_profile, indexed_vault) == 0
    assert read_vault_uri(vault_dir) is None  # marker gone


def test_drop_vault_keep_marker_preserves_yaml(indexed_vault, vault_dir, neo4j_profile):
    rc = cmd_drop(
        str(vault_dir), profile=None,
        dry_run=False, yes=True, keep_marker=True,
    )
    assert rc == 0
    assert _vault_doc_count(neo4j_profile, indexed_vault) == 0
    assert read_vault_uri(vault_dir) is not None  # marker preserved


def test_drop_dry_run_makes_no_changes(indexed_vault, vault_dir, neo4j_profile):
    before = _vault_doc_count(neo4j_profile, indexed_vault)
    rc = cmd_drop(
        str(vault_dir), profile=None,
        dry_run=True, yes=True, keep_marker=False,
    )
    assert rc == 0
    assert _vault_doc_count(neo4j_profile, indexed_vault) == before
    assert read_vault_uri(vault_dir) is not None


def test_drop_vault_by_slug_works(indexed_vault, vault_dir, neo4j_profile):
    """Passing a Vault.uri slug (no on-disk path) removes the vault by URI."""
    slug = indexed_vault  # the slug IS the URI
    rc = cmd_drop(
        slug, profile=None,
        dry_run=False, yes=True, keep_marker=False,
    )
    assert rc == 0
    assert _vault_doc_count(neo4j_profile, indexed_vault) == 0
    # Marker NOT touched when target was a slug (we don't know which path the
    # vault was at).
    assert read_vault_uri(vault_dir) is not None


def test_drop_unknown_slug_errors(indexed_vault, neo4j_profile):
    with pytest.raises(click.ClickException) as exc_info:
        cmd_drop(
            "does-not-exist", profile=None,
            dry_run=False, yes=True, keep_marker=False,
        )
    assert "not found" in exc_info.value.message


# ---- LINKS_TO orphan GC (the one edge case) -------------------------------


def test_drop_orphan_gc_removes_unresolved_wikilink_targets(
    tmp_path, neo4j_profile, cleanup_vault, monkeypatch,
):
    """When the removed vault's only WIKILINK_UNRESOLVED stub is dropped with
    the vault, it doesn't leak.

    In current 0.4.0, WIKILINK_UNRESOLVED Documents live INSIDE the source
    vault (HAS-attached to it), so they're removed by step 2 of the routine.
    No orphan GC needed for them — but this test pins the "no leaked stubs"
    invariant so a future refactor can't regress it.
    """
    vault = tmp_path / "linker"
    vault.mkdir()
    (vault / "a.md").write_text("References [[unknown-target]].\n\n# A\n\nbody.\n")
    res = ingest_vault(vault, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)

    # Set up config so cmd_drop runs cleanly.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("KI_PROFILE", raising=False)
    cfg = Config()
    cfg.add_profile(Profile(
        name=neo4j_profile.name, uri=neo4j_profile.uri,
        user=neo4j_profile.user, password=neo4j_profile.password,
    ))
    save_config(cfg)

    rc = cmd_drop(
        str(vault), profile=None,
        dry_run=False, yes=True, keep_marker=False,
    )
    assert rc == 0

    # No nodes (Vault, Document, Section, anything) survive from this vault.
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        row = session.run(
            "MATCH (n) WHERE n.uri STARTS WITH $prefix RETURN count(n) AS n",
            prefix=res.vault_uri + "/",
        ).single()
        assert row["n"] == 0
        row = session.run(
            "MATCH (v:Vault {uri: $uri}) RETURN count(v) AS n",
            uri=res.vault_uri,
        ).single()
        assert row["n"] == 0


# ---- --chunk-size flag passes through -------------------------------------


def test_drop_with_chunk_size_flag_works(indexed_vault, vault_dir, neo4j_profile):
    """--chunk-size is plumbed through to the batched-remove queries."""
    rc = cmd_drop(
        str(vault_dir), profile=None,
        dry_run=False, yes=True, keep_marker=False,
        chunk_size=128,
    )
    assert rc == 0
    assert _vault_doc_count(neo4j_profile, indexed_vault) == 0
