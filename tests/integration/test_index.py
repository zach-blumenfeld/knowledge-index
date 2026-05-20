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

    # Check the graph contents. The fixture has docs at multiple depths, so
    # walks of the form `(v)-[:HAS*]->(d:Document)` are required — only
    # root-level docs sit directly under the Vault.
    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            row = session.run(
                """
                MATCH (v:Vault {uri: $uri})-[:HAS*]->(d:Document)
                RETURN count(DISTINCT d) AS n_docs
                """,
                uri=result.vault_uri,
            ).single()
            assert row["n_docs"] == expected_files

            # All documents have at least one section (except no-headings.md)
            row = session.run(
                """
                MATCH (v:Vault {uri: $uri})-[:HAS*]->(d:Document)-[:HAS*]->(s:Section)
                RETURN count(DISTINCT s) AS n_sections
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
                MATCH (v:Vault {uri: $uri})-[:HAS*]->(d:Document)-[:HAS*]->(s:Section)-[:NEXT_SECTION]->(:Section)
                RETURN count(DISTINCT s) AS n
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
                "folder_uri_unique",
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
                MATCH (d:Document)-[:HAS*]->(s:Section)
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


def test_ki_index_with_description_flag_sets_property(tmp_path, neo4j_profile, cleanup_vault):
    """`ki index --description "..."` writes the YAML + propagates to Neo4j in one run."""
    from ki.commands.index import cmd_index

    fresh = tmp_path / "flag-vault"
    fresh.mkdir()
    (fresh / "n.md").write_text("# N\n\nbody.\n")

    # Drive cmd_index directly to avoid CliRunner's config-loading machinery.
    # We need a config on disk for it to find — write a minimal one pointing at
    # the live test Neo4j.
    import os

    import yaml as _yaml

    cfg_dir = tmp_path / "ki-config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        _yaml.safe_dump(
            {
                "default_profile": "test",
                "profiles": {
                    "test": {
                        "uri": neo4j_profile.uri,
                        "user": neo4j_profile.user,
                        "password": neo4j_profile.password,
                        "source": "existing",
                    }
                },
            }
        )
    )
    old_xdg = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_CONFIG_HOME"] = str(tmp_path)
    try:
        # `find_config_path` resolves ~/.config/ki/config.yaml; XDG_CONFIG_HOME
        # redirects ~/.config to our tmp dir, but config dir is named "ki/", not
        # "ki-config/". Rename to match.
        (cfg_dir).rename(tmp_path / "ki")

        rc = cmd_index(
            fresh,
            profile=None,
            batch_size=64,
            max_file_size=10 * 1024 * 1024,
            concurrency=4,
            yes=True,
            description="Vault for graph DB integration tests.",
            force_description=False,
        )
        assert rc == 0
    finally:
        if old_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = old_xdg

    # Marker should now have both uri: and description:
    marker = fresh / ".ki" / "vault.yaml"
    data = _yaml.safe_load(marker.read_text())
    assert "uri" in data
    assert data["description"] == "Vault for graph DB integration tests."

    cleanup_vault.append(data["uri"])

    # And Neo4j has it.
    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            row = session.run(
                "MATCH (v:Vault {uri: $u}) RETURN v.description AS d",
                u=data["uri"],
            ).single()
    assert row is not None
    assert "graph DB" in (row["d"] or "")


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


# --- :Folder layer (#17 phase 2b) ------------------------------------------


def _build_nasty_vault(root):
    """Stress fixture covering every shape the folder layer needs to handle.

    Layout:
      root.md                                      (root doc)
      another-root.md                              (sibling root doc)
      notes/one.md                                 (depth 1)
      notes/two.md                                 (sibling at depth 1)
      notes/three.md                               (third sibling)
      notes/projects/alpha.md                      (depth 2 — shared parent with archive/)
      notes/projects/beta.md                       (sibling at depth 2)
      notes/archive/old.md                         (sibling folder of projects/)
      deep/very/deeply/nested/directory/buried.md  (depth 6 — single chain)
      branch/a.md                                  (separate top-level branch)
      branch/sub/b.md                              (nested under branch/)
      empty/                                       (empty dir — should NOT materialize)
    """
    (root / "root.md").write_text("# Root\n\nbody.\n")
    (root / "another-root.md").write_text("# Another\n\nbody.\n")
    (root / "notes").mkdir()
    (root / "notes" / "one.md").write_text("# One\n\nbody.\n")
    (root / "notes" / "two.md").write_text("# Two\n\nbody.\n")
    (root / "notes" / "three.md").write_text("# Three\n\nbody.\n")
    (root / "notes" / "projects").mkdir()
    (root / "notes" / "projects" / "alpha.md").write_text("# Alpha\n")
    (root / "notes" / "projects" / "beta.md").write_text("# Beta\n")
    (root / "notes" / "archive").mkdir()
    (root / "notes" / "archive" / "old.md").write_text("# Old\n")
    deep = root / "deep" / "very" / "deeply" / "nested" / "directory"
    deep.mkdir(parents=True)
    (deep / "buried.md").write_text("# Buried\n")
    (root / "branch").mkdir()
    (root / "branch" / "a.md").write_text("# A\n")
    (root / "branch" / "sub").mkdir()
    (root / "branch" / "sub" / "b.md").write_text("# B\n")
    (root / "empty").mkdir()  # no docs — should NOT appear in graph


def test_folder_layer_nasty_structure(tmp_path, neo4j_profile, cleanup_vault):
    """End-to-end: index a multi-level vault, verify the :Folder layer."""
    fresh = tmp_path / "nasty-vault"
    fresh.mkdir()
    _build_nasty_vault(fresh)

    result = _run_ingest(fresh, neo4j_profile, batch_size=64)
    cleanup_vault.append(result.vault_uri)

    # 11 indexed docs, all the way down (2 root + 3 notes + 2 projects + 1 archive
    # + 1 deep chain + 1 branch root + 1 branch/sub).
    assert result.docs_added == 11

    # Expected folders (11):
    #   notes
    #   notes/projects
    #   notes/archive
    #   deep
    #   deep/very
    #   deep/very/deeply
    #   deep/very/deeply/nested
    #   deep/very/deeply/nested/directory
    #   branch
    #   branch/sub
    # Plus: `empty/` is NOT materialized (zero indexed docs under it).
    assert result.folders_total == 10

    v = result.vault_uri
    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            # All folders reachable from the Vault via HAS*.
            row = session.run(
                "MATCH (v:Vault {uri: $u})-[:HAS*]->(f:Folder) "
                "RETURN count(DISTINCT f) AS n",
                u=v,
            ).single()
            assert row["n"] == 10

            # Empty directory is NOT materialized.
            row = session.run(
                "MATCH (f:Folder {uri: $u + '/empty'}) RETURN f",
                u=v,
            ).single()
            assert row is None

            # Single-parent invariant: every Folder has exactly one incoming HAS.
            bad_folders = list(
                session.run(
                    """
                    MATCH (f:Folder) WHERE f.uri STARTS WITH $u + '/'
                    OPTIONAL MATCH ()-[r:HAS]->(f)
                    WITH f, count(r) AS in_count
                    WHERE in_count <> 1
                    RETURN f.uri AS uri, in_count
                    """,
                    u=v,
                )
            )
            assert bad_folders == [], f"folder single-parent invariant violated: {bad_folders}"

            # Single-parent invariant: every Document has exactly one incoming HAS.
            bad_docs = list(
                session.run(
                    """
                    MATCH (d:Document) WHERE d.uri STARTS WITH $u + '/'
                    OPTIONAL MATCH ()-[r:HAS]->(d)
                    WITH d, count(r) AS in_count
                    WHERE in_count <> 1
                    RETURN d.uri AS uri, in_count
                    """,
                    u=v,
                )
            )
            assert bad_docs == [], f"document single-parent invariant violated: {bad_docs}"

            # Root doc has a DIRECT Vault->HAS->Document edge (one hop).
            row = session.run(
                "MATCH (v:Vault {uri: $u})-[:HAS]->(d:Document {uri: $u + '/root.md'}) RETURN d",
                u=v,
            ).single()
            assert row is not None, "expected direct Vault->HAS->Document for root.md"

            # Nested doc has NO direct Vault->HAS->Document edge (must go via folders).
            row = session.run(
                """
                MATCH (v:Vault {uri: $u})-[:HAS]->(d:Document)
                WHERE d.uri = $u + '/notes/projects/alpha.md'
                RETURN d
                """,
                u=v,
            ).single()
            assert row is None, "nested doc should not have a direct Vault edge"

            # Nested doc IS reachable via the folder chain (Vault -> notes -> projects -> alpha).
            row = session.run(
                """
                MATCH path = (v:Vault {uri: $u})-[:HAS*]->(d:Document)
                WHERE d.uri = $u + '/notes/projects/alpha.md'
                RETURN length(path) AS hops
                """,
                u=v,
            ).single()
            assert row is not None and row["hops"] == 3, (
                f"alpha.md should be 3 hops from Vault (notes -> projects -> doc); "
                f"got {row['hops'] if row else None}"
            )

            # Depth-6 buried doc is reachable through its full folder chain.
            row = session.run(
                """
                MATCH path = (v:Vault {uri: $u})-[:HAS*]->(d:Document)
                WHERE d.uri ENDS WITH '/buried.md'
                RETURN length(path) AS hops
                """,
                u=v,
            ).single()
            assert row["hops"] == 6, (
                f"buried.md is 5 folder hops + 1 doc hop = 6; got {row['hops']}"
            )

            # Sibling folders under `notes/` both exist with shared parent.
            row = session.run(
                """
                MATCH (parent:Folder {uri: $u + '/notes'})-[:HAS]->(child:Folder)
                RETURN collect(child.name) AS names
                """,
                u=v,
            ).single()
            assert set(row["names"]) == {"projects", "archive"}

            # The `notes` folder also owns three documents (HAS to docs).
            row = session.run(
                """
                MATCH (parent:Folder {uri: $u + '/notes'})-[:HAS]->(d:Document)
                RETURN count(d) AS n
                """,
                u=v,
            ).single()
            assert row["n"] == 3  # one.md, two.md, three.md (NOT alpha/beta/old)


def test_folder_layer_reindex_is_idempotent(tmp_path, neo4j_profile, cleanup_vault):
    """A second ingest with no filesystem changes must not duplicate folders/edges."""
    fresh = tmp_path / "idempotent-vault"
    fresh.mkdir()
    _build_nasty_vault(fresh)

    first = _run_ingest(fresh, neo4j_profile, batch_size=64)
    cleanup_vault.append(first.vault_uri)
    assert first.folders_total == 10

    second = _run_ingest(fresh, neo4j_profile, batch_size=64)
    assert second.folders_total == 10  # same count — MERGE is idempotent
    assert second.docs_added == 0
    assert second.docs_updated == 0
    assert second.docs_skipped_unchanged == 11

    v = first.vault_uri
    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            # No duplicate Folder nodes (would imply MERGE-key bug).
            row = session.run(
                "MATCH (f:Folder) WHERE f.uri STARTS WITH $u + '/' RETURN count(f) AS n",
                u=v,
            ).single()
            assert row["n"] == 10

            # No duplicate HAS edges incident to any Folder.
            row = session.run(
                """
                MATCH (f:Folder) WHERE f.uri STARTS WITH $u + '/'
                MATCH ()-[r:HAS]->(f)
                WITH f, count(r) AS in_count
                WHERE in_count > 1
                RETURN count(f) AS dups
                """,
                u=v,
            ).single()
            assert row["dups"] == 0


def test_folder_layer_root_only_vault_has_no_folders(tmp_path, neo4j_profile, cleanup_vault):
    """A vault with only root-level docs creates zero Folder nodes."""
    fresh = tmp_path / "flat-vault"
    fresh.mkdir()
    (fresh / "a.md").write_text("# A\n")
    (fresh / "b.md").write_text("# B\n")
    (fresh / "c.md").write_text("# C\n")

    result = _run_ingest(fresh, neo4j_profile, batch_size=64)
    cleanup_vault.append(result.vault_uri)
    assert result.folders_total == 0

    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            row = session.run(
                "MATCH (v:Vault {uri: $u})-[:HAS*]->(f:Folder) RETURN count(f) AS n",
                u=result.vault_uri,
            ).single()
            assert row["n"] == 0

            # Each root doc gets a direct Vault->HAS->Document edge.
            row = session.run(
                "MATCH (v:Vault {uri: $u})-[:HAS]->(d:Document) RETURN count(d) AS n",
                u=result.vault_uri,
            ).single()
            assert row["n"] == 3


def test_folder_layer_wikilink_resolver_finds_nested_docs(tmp_path, neo4j_profile, cleanup_vault):
    """A wikilink from a root-level doc must resolve to a doc nested several levels deep."""
    fresh = tmp_path / "wikilink-vault"
    fresh.mkdir()
    (fresh / "index.md").write_text("# Index\n\nSee [[target]] and [[Other]].\n")
    nested = fresh / "buried" / "deep" / "down"
    nested.mkdir(parents=True)
    (nested / "target.md").write_text("# Target\n\nbody.\n")
    (fresh / "buried" / "Other.md").write_text("# Other\n\nbody.\n")

    # First ingest establishes the graph.
    first = _run_ingest(fresh, neo4j_profile, batch_size=64)
    cleanup_vault.append(first.vault_uri)

    # Re-ingest with a clean fileHash so the resolver loads via the graph
    # (it would otherwise also work from in-memory state during the first run).
    (fresh / "index.md").write_text(
        "# Index\n\nSee [[target]] and [[Other]] for details.\n"
    )
    second = _run_ingest(fresh, neo4j_profile, batch_size=64)
    assert second.docs_updated == 1

    v = first.vault_uri
    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            # `index.md` resolved both wikilinks (one to a 3-level-deep doc,
            # one to a 1-level-deep sibling-of-the-folder doc). The actual
            # `LINKS_TO` source is the H1 Section inside `index.md` (that's
            # where the wikilink text lives), so we walk the section tree
            # to find every origin attached to this Document.
            row = session.run(
                """
                MATCH (src:Document {uri: $u + '/index.md'})
                MATCH (src)-[:HAS*0..]->(origin)
                MATCH (origin)-[:LINKS_TO]->(tgt:Document)
                RETURN collect(DISTINCT tgt.uri) AS tgts
                """,
                u=v,
            ).single()
            tgts = set(row["tgts"])
            assert f"{v}/buried/deep/down/target.md" in tgts
            assert f"{v}/buried/other.md" in tgts


def test_folder_layer_rm_vault_clears_folders_too(tmp_path, neo4j_profile):
    """`ki rm --vault` deletes the Folder nodes alongside Documents and Sections."""
    from ki.ingest import queries as Q

    fresh = tmp_path / "rm-folders-vault"
    fresh.mkdir()
    _build_nasty_vault(fresh)
    result = _run_ingest(fresh, neo4j_profile, batch_size=64)
    v = result.vault_uri

    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            session.run(Q.DELETE_VAULT, vaultUri=v).consume()

            # Vault is gone.
            row = session.run("MATCH (v:Vault {uri: $u}) RETURN v", u=v).single()
            assert row is None

            # No Folder nodes from this vault survive.
            row = session.run(
                "MATCH (f:Folder) WHERE f.uri STARTS WITH $u + '/' RETURN count(f) AS n",
                u=v,
            ).single()
            assert row["n"] == 0

            # Documents gone too.
            row = session.run(
                "MATCH (d:Document) WHERE d.uri STARTS WITH $u + '/' RETURN count(d) AS n",
                u=v,
            ).single()
            assert row["n"] == 0
