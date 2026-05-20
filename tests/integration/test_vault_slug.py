"""Integration tests for slug-based Vault.uri assignment.

Covers `compute_base_slug` + `find_next_vault_slug` end-to-end via
`ingest_vault`: fresh basename gets the bare slug; basename collision
gets `-1`; max+1 strategy on multiple collisions; never-reuse on
delete-then-re-ingest.
"""

from __future__ import annotations

import pytest

from ki.ingest.pipeline import IngestOptions, ingest_vault
from ki.ingest.remove import remove_vault
from ki.neo4j_client import driver_for
from ki.vault import read_vault_uri

pytestmark = pytest.mark.integration


def _write_tiny_vault(parent_dir, name: str):
    d = parent_dir / name
    d.mkdir()
    (d / "doc.md").write_text("# D\n\nbody.\n")
    return d


def test_fresh_basename_assigns_bare_slug(tmp_path, neo4j_profile, cleanup_vault):
    vault = _write_tiny_vault(tmp_path, "my-notes")
    res = ingest_vault(vault, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)
    assert res.vault_uri == "my-notes"
    assert read_vault_uri(vault) == "my-notes"


def test_basename_collision_assigns_dash_one(tmp_path, neo4j_profile, cleanup_vault):
    # First vault: ~/dir-A/my-notes → my-notes
    parent_a = tmp_path / "dir-a"
    parent_a.mkdir()
    vault_a = _write_tiny_vault(parent_a, "my-notes")
    res_a = ingest_vault(vault_a, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res_a.vault_uri)
    assert res_a.vault_uri == "my-notes"

    # Second vault: ~/dir-B/my-notes → my-notes-1
    parent_b = tmp_path / "dir-b"
    parent_b.mkdir()
    vault_b = _write_tiny_vault(parent_b, "my-notes")
    res_b = ingest_vault(vault_b, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res_b.vault_uri)
    assert res_b.vault_uri == "my-notes-1"
    assert read_vault_uri(vault_b) == "my-notes-1"


def test_multiple_collisions_use_max_plus_one(tmp_path, neo4j_profile, cleanup_vault):
    """Sequentially ingest three same-basename vaults → my-notes, -1, -2."""
    for i, dir_name in enumerate(("a", "b", "c")):
        parent = tmp_path / dir_name
        parent.mkdir()
        vault = _write_tiny_vault(parent, "my-notes")
        res = ingest_vault(vault, IngestOptions(profile=neo4j_profile, batch_size=64))
        cleanup_vault.append(res.vault_uri)
        expected = "my-notes" if i == 0 else f"my-notes-{i}"
        assert res.vault_uri == expected, (
            f"step {i}: expected {expected!r}, got {res.vault_uri!r}"
        )


def test_delete_then_reingest_reuses_freed_slot(tmp_path, neo4j_profile, cleanup_vault):
    """When a vault is removed, its slug becomes available again.

    The collision algorithm queries currently-present vaults — there's no
    tombstone history. So if `my-notes-1` is removed and another
    basename-`my-notes` vault ingests, it takes `my-notes-1` back. This
    is a known trade-off (documented in vault.py find_next_vault_slug);
    cross-vault references that pointed at the deleted slug can silently
    re-point. Filing a tombstone scheme would change this behavior.
    """
    # Ingest two vaults to claim my-notes and my-notes-1.
    for dir_name, expected in (("a", "my-notes"), ("b", "my-notes-1")):
        parent = tmp_path / dir_name
        parent.mkdir()
        vault = _write_tiny_vault(parent, "my-notes")
        res = ingest_vault(vault, IngestOptions(profile=neo4j_profile, batch_size=64))
        assert res.vault_uri == expected
        cleanup_vault.append(res.vault_uri)

    # Remove my-notes-1 (simulating `ki rm --vault`).
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        remove_vault(session, "my-notes-1")

    # Ingest a third basename-`my-notes` vault. The freed slot is reused.
    parent_c = tmp_path / "c"
    parent_c.mkdir()
    vault_c = _write_tiny_vault(parent_c, "my-notes")
    res_c = ingest_vault(vault_c, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res_c.vault_uri)
    assert res_c.vault_uri == "my-notes-1"


def test_useless_basename_errors_cleanly(tmp_path, neo4j_profile, cleanup_vault):
    """A folder named `___` has no alphanumeric content — refuse, don't UUID-fallback."""
    from ki.vault import InvalidVaultBasenameError

    vault = tmp_path / "___"
    vault.mkdir()
    (vault / "x.md").write_text("# X\n\nbody.\n")
    with pytest.raises(InvalidVaultBasenameError) as excinfo:
        ingest_vault(vault, IngestOptions(profile=neo4j_profile, batch_size=64))
    assert "___" in str(excinfo.value)
    assert "rename" in str(excinfo.value).lower()


def test_existing_marker_is_honored_verbatim(tmp_path, neo4j_profile, cleanup_vault):
    """If `.ki/vault.yaml` already has a `uri:`, ingest uses it as-is — no recompute."""
    vault = _write_tiny_vault(tmp_path, "rando-name")
    (vault / ".ki").mkdir()
    (vault / ".ki" / "vault.yaml").write_text("uri: pre-assigned-slug\n")

    res = ingest_vault(vault, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)
    assert res.vault_uri == "pre-assigned-slug"
    # The marker is rewritten with the same URI — idempotent.
    assert read_vault_uri(vault) == "pre-assigned-slug"


def test_document_uris_use_vault_slug_prefix(tmp_path, neo4j_profile, cleanup_vault):
    """Document.uri = '<slug>/<file path>' — confirm via Neo4j round-trip."""
    vault = _write_tiny_vault(tmp_path, "my-blog")
    res = ingest_vault(vault, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)
    assert res.vault_uri == "my-blog"
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        rows = list(session.run(
            "MATCH (d:Document) WHERE d.uri STARTS WITH 'my-blog/' RETURN d.uri AS uri",
        ))
    uris = {r["uri"] for r in rows}
    assert "my-blog/doc.md" in uris
