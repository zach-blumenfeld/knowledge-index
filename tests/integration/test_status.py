"""Integration: `ki status` reachable-Neo4j layers (NOT_INDEXED / READY / STALE)."""

from __future__ import annotations

import pytest

from ki.config import Config, Profile
from ki.ingest.pipeline import IngestOptions, ingest_vault
from ki.status import NOT_INDEXED, READY, STALE, compute_status
from ki.vault import write_vault_marker

pytestmark = pytest.mark.integration


def _cfg(neo4j_profile) -> Config:
    cfg = Config()
    cfg.add_profile(Profile(
        name=neo4j_profile.name, uri=neo4j_profile.uri,
        user=neo4j_profile.user, password=neo4j_profile.password,
    ))
    return cfg


def test_status_not_indexed_when_marker_but_no_vault(tmp_path, neo4j_profile):
    # Marker bound to a reachable profile, but nothing indexed under this uri.
    write_vault_marker(tmp_path, uri="ghost-vault-xyz", profile=neo4j_profile.name)
    res = compute_status(_cfg(neo4j_profile), tmp_path)
    assert res.state == NOT_INDEXED


def test_status_ready_then_stale(vault_dir, neo4j_profile, cleanup_vault):
    # ingest_vault writes the marker bound to neo4j_profile.name.
    res = ingest_vault(vault_dir, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)
    cfg = _cfg(neo4j_profile)

    ready = compute_status(cfg, vault_dir)
    assert ready.state == READY
    assert ready.vault_uri == res.vault_uri

    # Add a brand-new markdown file → set check trips STALE (added).
    (vault_dir / "brand-new-note.md").write_text("# New\n\nbody\n", encoding="utf-8")
    stale_add = compute_status(cfg, vault_dir)
    assert stale_add.state == STALE
    assert stale_add.detail["added"] == 1

    # Re-index, then edit an existing file in place → hash check trips STALE.
    ingest_vault(vault_dir, IngestOptions(profile=neo4j_profile, batch_size=64))
    assert compute_status(cfg, vault_dir).state == READY
    (vault_dir / "brand-new-note.md").write_text("# New\n\nEDITED\n", encoding="utf-8")
    stale_edit = compute_status(cfg, vault_dir)
    assert stale_edit.state == STALE
    assert stale_edit.detail["changed"] == 1
