"""Integration tests for #37 — capture all markdown links as :Document nodes.

Covers stub creation for internal non-md files, external URL Documents,
folder materialization for non-md-only directories, missing-file warn+skip,
and vault-escaping internal links treated as external file://...
"""

from __future__ import annotations

import pytest

from ki.ingest.pipeline import IngestOptions, ingest_vault
from ki.neo4j_client import driver_for

pytestmark = pytest.mark.integration


def _run_ingest(vault_dir, neo4j_profile, **opts):
    return ingest_vault(
        vault_dir, IngestOptions(profile=neo4j_profile, batch_size=64, **opts)
    )


# ---- External URLs --------------------------------------------------------


def test_external_url_creates_external_document(tmp_path, neo4j_profile, cleanup_vault):
    vault = tmp_path / "ext-url-vault"
    vault.mkdir()
    (vault / "blog.md").write_text(
        "# Blog\n\nSee [Launch blog](https://neo4j.com/blog/agentic-ai/) for details.\n"
    )
    res = _run_ingest(vault, neo4j_profile)
    cleanup_vault.append(res.vault_uri)

    with driver_for(neo4j_profile) as driver, driver.session() as session:
        row = session.run(
            "MATCH (d:Document {uri: $u}) RETURN d.sourceType AS st, "
            "d.displayName AS dn, d.path AS p, d.fileHash AS fh",
            u="https://neo4j.com/blog/agentic-ai/",
        ).single()
        assert row is not None, "external Document not created"
        assert row["st"] == "URL_LINK"
        assert row["p"] is None
        assert row["fh"] is None

        # No HAS edge to any Vault (external Documents live outside the tree).
        row = session.run(
            "MATCH (:Vault)-[:HAS*]->(d:Document {uri: $u}) RETURN count(d) AS n",
            u="https://neo4j.com/blog/agentic-ai/",
        ).single()
        assert row["n"] == 0

        # LINKS_TO edge from the section inside blog.md to the external Document.
        row = session.run(
            "MATCH (s:Section)-[:LINKS_TO]->(t:Document {uri: $u}) "
            "RETURN count(s) AS n",
            u="https://neo4j.com/blog/agentic-ai/",
        ).single()
        assert row["n"] >= 1


def test_external_url_link_text_becomes_alias(tmp_path, neo4j_profile, cleanup_vault):
    """Per #37 q3: the `[text]` part populates the target's `aliases` list."""
    vault = tmp_path / "alias-url-vault"
    vault.mkdir()
    (vault / "post.md").write_text(
        "# Post\n\nSee [Aura Agent launch](https://neo4j.com/product/aura-agent/).\n"
    )
    res = _run_ingest(vault, neo4j_profile)
    cleanup_vault.append(res.vault_uri)

    with driver_for(neo4j_profile) as driver, driver.session() as session:
        row = session.run(
            "MATCH (d:Document {uri: $u}) RETURN d.aliases AS aliases",
            u="https://neo4j.com/product/aura-agent/",
        ).single()
    assert "Aura Agent launch" in (row["aliases"] or [])


def test_cross_vault_external_url_collapses_to_one_node(
    tmp_path, neo4j_profile, cleanup_vault,
):
    """Same URL from two vaults → one :Document with LINKS_TO from both."""
    url = "https://example.com/shared-link"
    for name in ("vault-a", "vault-b"):
        v = tmp_path / name
        v.mkdir()
        (v / "n.md").write_text(f"# N\n\nsee [link]({url}).\n")
        res = _run_ingest(v, neo4j_profile)
        cleanup_vault.append(res.vault_uri)

    with driver_for(neo4j_profile) as driver, driver.session() as session:
        row = session.run(
            "MATCH (d:Document {uri: $u}) RETURN count(d) AS n",
            u=url,
        ).single()
        assert row["n"] == 1  # cross-vault MERGE collapse

        # LINKS_TO from both vaults.
        row = session.run(
            "MATCH (s:Section)-[:LINKS_TO]->(d:Document {uri: $u}) "
            "MATCH (v:Vault)-[:HAS*]->(s) "
            "RETURN count(DISTINCT v) AS n",
            u=url,
        ).single()
        assert row["n"] == 2


# ---- Internal non-md stub Documents ---------------------------------------


def test_internal_non_md_creates_stub_document(tmp_path, neo4j_profile, cleanup_vault):
    vault = tmp_path / "stub-vault"
    vault.mkdir()
    (vault / "presentations").mkdir()
    (vault / "presentations" / "q3-deck.pptx").write_bytes(b"fake pptx bytes\n")
    (vault / "post.md").write_text(
        "# Post\n\nSee [Q3 deck](./presentations/q3-deck.pptx) for numbers.\n"
    )
    res = _run_ingest(vault, neo4j_profile)
    cleanup_vault.append(res.vault_uri)

    stub_uri = f"{res.vault_uri}/presentations/q3-deck.pptx"
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        row = session.run(
            "MATCH (d:Document {uri: $u}) "
            "RETURN d.sourceType AS st, d.path AS p, d.fileHash AS fh, "
            "d.name AS name, d.aliases AS aliases",
            u=stub_uri,
        ).single()
        assert row is not None, "stub Document not created"
        assert row["st"] == "LOCAL_FILE"
        assert row["p"].endswith("q3-deck.pptx")
        assert row["fh"] is not None and len(row["fh"]) == 64  # sha256 hex
        assert row["name"] == "q3-deck.pptx"
        assert "Q3 deck" in (row["aliases"] or [])

        # HAS chain: Vault -> presentations Folder -> Document.
        row = session.run(
            "MATCH (v:Vault {uri: $v})-[:HAS]->(f:Folder)-[:HAS]->(d:Document {uri: $u}) "
            "RETURN f.uri AS folder_uri, f.name AS folder_name",
            v=res.vault_uri, u=stub_uri,
        ).single()
        assert row is not None
        assert row["folder_name"] == "presentations"

        # LINKS_TO edge.
        row = session.run(
            "MATCH (s:Section)-[:LINKS_TO]->(d:Document {uri: $u}) RETURN count(s) AS n",
            u=stub_uri,
        ).single()
        assert row["n"] >= 1


def test_non_md_only_directory_gets_folder_materialized(
    tmp_path, neo4j_profile, cleanup_vault,
):
    """A directory containing only non-md files materializes as a Folder when linked."""
    vault = tmp_path / "non-md-folder-vault"
    vault.mkdir()
    (vault / "assets").mkdir()
    (vault / "assets" / "spec.docx").write_bytes(b"fake docx\n")
    (vault / "readme.md").write_text(
        "# Readme\n\n[Spec](./assets/spec.docx) lives in assets/.\n"
    )
    res = _run_ingest(vault, neo4j_profile)
    cleanup_vault.append(res.vault_uri)

    with driver_for(neo4j_profile) as driver, driver.session() as session:
        # The `assets/` folder is materialized purely because of the stub.
        row = session.run(
            "MATCH (f:Folder {uri: $u}) RETURN f.name AS name",
            u=f"{res.vault_uri}/assets",
        ).single()
        assert row is not None, "Folder for non-md-only directory not materialized"
        assert row["name"] == "assets"


def test_missing_internal_file_warn_skip(
    tmp_path, neo4j_profile, cleanup_vault, caplog,
):
    """Link to ./missing.pptx — file doesn't exist; warn + skip (no stub created)."""
    import logging

    vault = tmp_path / "missing-vault"
    vault.mkdir()
    (vault / "n.md").write_text("# N\n\nsee [Missing](./does-not-exist.pptx).\n")
    with caplog.at_level(logging.WARNING, logger="ki.ingest.pipeline"):
        res = _run_ingest(vault, neo4j_profile)
    cleanup_vault.append(res.vault_uri)

    # No stub Document created for the missing file.
    expected_stub = f"{res.vault_uri}/does-not-exist.pptx"
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        row = session.run(
            "MATCH (d:Document {uri: $u}) RETURN count(d) AS n",
            u=expected_stub,
        ).single()
        assert row["n"] == 0

    # Warning fired.
    assert any(
        "does not exist on disk" in r.message for r in caplog.records
    ), [r.message for r in caplog.records]


def test_vault_escaping_internal_path_becomes_external(
    tmp_path, neo4j_profile, cleanup_vault,
):
    """`[Slides](../outside/file.pptx)` resolves outside the vault → external file://..."""
    (tmp_path / "outside").mkdir()
    outside_file = tmp_path / "outside" / "external.pptx"
    outside_file.write_bytes(b"outside the vault\n")

    vault = tmp_path / "escape-vault"
    vault.mkdir()
    (vault / "n.md").write_text(
        "# N\n\nsee [External](../outside/external.pptx) outside.\n"
    )
    res = _run_ingest(vault, neo4j_profile)
    cleanup_vault.append(res.vault_uri)

    expected_uri = f"file://{outside_file.resolve().as_posix()}"
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        row = session.run(
            "MATCH (d:Document {uri: $u}) "
            "RETURN d.sourceType AS st, d.path AS p, d.aliases AS aliases",
            u=expected_uri,
        ).single()
        assert row is not None, f"external file:// Document not created at {expected_uri}"
        assert row["st"] == "URL_LINK"
        assert row["p"] is None
        assert "External" in (row["aliases"] or [])


# ---- Re-index removes stale LINKS_TO but keeps shared stub/external -------


def test_reindex_after_link_removed_drops_edge(tmp_path, neo4j_profile, cleanup_vault):
    """Vault-level resync: if a link is removed from disk, LINKS_TO is dropped.

    External Document survives because vault-level sync only removes vault
    contents; the external lives outside any vault.
    """
    vault = tmp_path / "reindex-vault"
    vault.mkdir()
    (vault / "n.md").write_text(
        "# N\n\nsee [linked](https://example.com/persisted).\n"
    )
    res = _run_ingest(vault, neo4j_profile)
    cleanup_vault.append(res.vault_uri)

    # Verify the edge exists.
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        row = session.run(
            "MATCH ()-[r:LINKS_TO]->(:Document {uri: 'https://example.com/persisted'}) "
            "RETURN count(r) AS n"
        ).single()
        assert row["n"] >= 1

    # Edit to remove the link.
    (vault / "n.md").write_text("# N\n\nno link anymore.\n")
    _run_ingest(vault, neo4j_profile)

    with driver_for(neo4j_profile) as driver, driver.session() as session:
        # LINKS_TO edge dropped (its source section was nuked + re-ingested
        # without the link).
        row = session.run(
            "MATCH ()-[r:LINKS_TO]->(:Document {uri: 'https://example.com/persisted'}) "
            "RETURN count(r) AS n"
        ).single()
        assert row["n"] == 0
        # External Document SURVIVES the vault re-ingest (it has no HAS edge
        # to the removed vault). It's now degree-zero — orphan GC will pick
        # it up via the existing remove_vault routine's snapshot-and-recheck.
        # Actually, depending on whether the vault remove step caught it:
        # it should have been in the snapshot (we had an outbound LINKS_TO
        # at remove-time), then orphan-GC'd. Confirm.
        row = session.run(
            "MATCH (d:Document {uri: 'https://example.com/persisted'}) "
            "RETURN count(d) AS n"
        ).single()
        # Orphan GC kicks in: degree-zero after subtree removal, so removed.
        assert row["n"] == 0
