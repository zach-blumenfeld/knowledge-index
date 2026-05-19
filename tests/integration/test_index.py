"""Integration tests for `ki index` against an ephemeral Neo4j."""

from __future__ import annotations

import pytest

from ki.ingest.pipeline import IngestOptions, ingest_vault
from ki.neo4j_client import driver_for
from ki.vault import read_vault_id

pytestmark = pytest.mark.integration


def _vault_md_count(vault_dir):
    """Count markdown files the indexer will see (excluding `.ki/` etc)."""
    from ki.vault import iter_markdown_files

    return len(iter_markdown_files(vault_dir))


def _run_ingest(vault_dir, neo4j_profile, **opts):
    options = IngestOptions(profile=neo4j_profile, **opts)
    return ingest_vault(vault_dir, options)


def test_first_index_creates_nodes_and_edges(vault_dir, neo4j_profile, cleanup_vault):
    expected_files = _vault_md_count(vault_dir)
    # The generator commits a `.ki/vault.yaml` into the fixture so the vault is
    # already initialised before this test runs; we don't assert vault_created.
    result = _run_ingest(vault_dir, neo4j_profile, batch_size=64)
    cleanup_vault.append(result.vault_uri)

    assert result.docs_added == expected_files
    assert result.docs_updated == 0
    assert result.sections_written > expected_files  # multiple sections per doc
    assert read_vault_id(vault_dir) == result.vault_uri

    # Check the graph contents.
    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            row = session.run(
                """
                MATCH (v:Vault {uri: $uri})-[:HAS_DOCUMENT]->(d:Document)
                RETURN count(d) AS n_docs
                """,
                uri=result.vault_uri,
            ).single()
            assert row["n_docs"] == expected_files

            # All documents have at least one section (except no-headings.md)
            row = session.run(
                """
                MATCH (v:Vault {uri: $uri})-[:HAS_DOCUMENT]->(:Document)-[:HAS_SECTION]->(s:Section)
                RETURN count(s) AS n_sections
                """,
                uri=result.vault_uri,
            ).single()
            assert row["n_sections"] > 0

            # USES_VAULT edge from User
            row = session.run(
                """
                MATCH (u:User)-[:USES_VAULT]->(v:Vault {uri: $uri})
                RETURN count(u) AS n_users
                """,
                uri=result.vault_uri,
            ).single()
            assert row["n_users"] == 1

            # Vault-level LOADED edge
            row = session.run(
                """
                MATCH (u:User)-[l:LOADED]->(v:Vault {uri: $uri})
                RETURN count(l) AS n
                """,
                uri=result.vault_uri,
            ).single()
            assert row["n"] >= 1

            # NEXT_SECTION chain exists for at least one doc
            row = session.run(
                """
                MATCH (v:Vault {uri: $uri})-[:HAS_DOCUMENT]->(d)-[:HAS_SECTION*]->(s)-[:NEXT_SECTION]->(:Section)
                RETURN count(s) AS n
                """,
                uri=result.vault_uri,
            ).single()
            assert row["n"] > 0

            # Constraints + fulltext index present
            constraints = list(session.run("SHOW CONSTRAINTS YIELD name RETURN name"))
            names = {row["name"] for row in constraints}
            for expected in (
                "user_id_unique",
                "vault_uri_unique",
                "document_uri_unique",
                "section_uri_unique",
            ):
                assert expected in names, f"missing constraint {expected}"

            indexes = list(session.run("SHOW INDEXES YIELD name RETURN name"))
            assert "content_search" in {row["name"] for row in indexes}


def test_reindex_unchanged_is_noop(vault_dir, neo4j_profile, cleanup_vault):
    expected_files = _vault_md_count(vault_dir)
    first = _run_ingest(vault_dir, neo4j_profile, batch_size=64)
    cleanup_vault.append(first.vault_uri)

    second = _run_ingest(vault_dir, neo4j_profile, batch_size=64)
    assert second.docs_added == 0
    assert second.docs_updated == 0
    assert second.docs_skipped_unchanged == expected_files


def test_reindex_after_edit_updates_only_that_doc(vault_dir, neo4j_profile, cleanup_vault):
    expected_files = _vault_md_count(vault_dir)
    first = _run_ingest(vault_dir, neo4j_profile, batch_size=64)
    cleanup_vault.append(first.vault_uri)

    # Edit one file. The generated fixture always carries this one (see
    # scripts/gen_test_vault.py / Big Idea); falls through to any .md otherwise.
    target = vault_dir / "Notes" / "My Projects" / "big-idea.md"
    if not target.exists():
        target = next(vault_dir.rglob("*.md"))
    before_sections = _count_sections_for_doc(neo4j_profile, first.vault_uri, target.name)
    target.write_text(target.read_text() + "\n## NEW Section\n\nfresh content.\n")

    second = _run_ingest(vault_dir, neo4j_profile, batch_size=64)
    assert second.docs_added == 0
    assert second.docs_updated == 1
    assert second.docs_skipped_unchanged == expected_files - 1

    after_sections = _count_sections_for_doc(neo4j_profile, first.vault_uri, target.name)
    assert after_sections >= before_sections + 1


def _count_sections_for_doc(neo4j_profile, vault_uri, doc_name):
    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            row = session.run(
                """
                MATCH (d:Document)-[:HAS_SECTION*]->(s:Section)
                WHERE d.uri STARTS WITH $vault AND d.name = $name
                RETURN count(s) AS n
                """,
                vault=vault_uri,
                name=doc_name,
            ).single()
            return row["n"] if row else 0


def test_first_index_of_fresh_dir_creates_marker(tmp_path, neo4j_profile, cleanup_vault):
    """Auto-sense: missing `.ki/vault.yaml` → marker created on first index.

    The committed fixture pre-bakes its marker, so the standard `vault_dir`
    fixture can't exercise this branch. Build a fresh dir here.
    """
    fresh = tmp_path / "fresh-vault"
    fresh.mkdir()
    (fresh / "one.md").write_text("# One\n\nbody one.\n")
    (fresh / "two.md").write_text("# Two\n\nbody two.\n")

    assert read_vault_id(fresh) is None  # precondition: no marker

    result = _run_ingest(fresh, neo4j_profile, batch_size=64)
    cleanup_vault.append(result.vault_uri)

    assert result.vault_created is True
    assert read_vault_id(fresh) == result.vault_uri
    assert result.docs_added == 2
    # The marker should be `.ki/vault.yaml`, not the legacy bare-UUID file.
    assert (fresh / ".ki" / "vault.yaml").exists()
    assert not (fresh / ".ki" / "vault-id").exists()
    # A fresh vault has no description yet — flag so `ki index` can prompt.
    assert result.vault_description_set is False


def test_ingest_sets_description_from_yaml(tmp_path, neo4j_profile, cleanup_vault):
    """User-authored `description:` in `.ki/vault.yaml` ends up on `:Vault`."""
    import yaml as _yaml

    fresh = tmp_path / "desc-vault"
    fresh.mkdir()
    (fresh / "n.md").write_text("# N\n\nbody.\n")
    (fresh / ".ki").mkdir()
    marker = fresh / ".ki" / "vault.yaml"
    # Write the marker by hand so the description is present on first ingest.
    import uuid

    seeded_uri = str(uuid.uuid4())
    marker.write_text(
        _yaml.safe_dump(
            {
                "uri": seeded_uri,
                "description": "Personal notes on graph databases and Neo4j.",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = _run_ingest(fresh, neo4j_profile, batch_size=64)
    cleanup_vault.append(result.vault_uri)
    assert result.vault_uri == seeded_uri
    assert result.vault_description_set is True

    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            row = session.run(
                "MATCH (v:Vault {uri: $u}) RETURN v.description AS d",
                u=result.vault_uri,
            ).single()
    assert row is not None
    assert "graph databases" in (row["d"] or "")


def test_oversize_files_skipped_with_summary(vault_dir, neo4j_profile, cleanup_vault):
    big = vault_dir / "inbox" / "huge.md"
    big.parent.mkdir(parents=True, exist_ok=True)
    big.write_text("# Huge\n" + ("x" * 1024 + "\n") * 200)  # ~200 KB
    result = _run_ingest(vault_dir, neo4j_profile, batch_size=64, max_file_size=50_000)
    cleanup_vault.append(result.vault_uri)
    assert result.docs_skipped_oversize == 1
    assert big in result.oversize_files
