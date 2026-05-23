"""Integration tests for `ki index` against an ephemeral Neo4j."""

from __future__ import annotations

import pytest

from ki.ingest.pipeline import IngestOptions, ingest_vault
from ki.neo4j_client import driver_for
from ki.vault import read_vault_uri

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
    assert read_vault_uri(vault_dir) == result.vault_uri

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


def test_reindex_is_full_rebuild(vault_dir, neo4j_profile, cleanup_vault):
    """Per `docs/index_rm_behavior.md`, re-indexing an existing vault nukes
    the vault contents first, then re-ingests everything. Every doc is
    counted as "added" on the second pass — the incremental fileHash-skip
    optimization no longer fires because there's nothing to skip-against.
    """
    expected_files = _vault_md_count(vault_dir)
    first = _run_ingest(vault_dir, neo4j_profile, batch_size=64)
    cleanup_vault.append(first.vault_uri)

    second = _run_ingest(vault_dir, neo4j_profile, batch_size=64)
    assert second.docs_added == expected_files
    assert second.docs_updated == 0
    assert second.docs_skipped_unchanged == 0
    # Same vault URI — marker is honored on the re-ingest path.
    assert second.vault_uri == first.vault_uri


def test_reindex_picks_up_disk_changes(vault_dir, neo4j_profile, cleanup_vault):
    """Vault-level sync: disk changes (edits, new files) land on re-index.

    Tests the round-trip: index → edit → re-index → graph reflects edit.
    Previously this asserted the fileHash-skip-everything-except-N
    semantics; now it asserts the simpler vault-level-sync invariant.
    """
    expected_files = _vault_md_count(vault_dir)
    first = _run_ingest(vault_dir, neo4j_profile, batch_size=64)
    cleanup_vault.append(first.vault_uri)

    target = vault_dir / "Notes" / "My Projects" / "big-idea.md"
    if not target.exists():
        target = next(vault_dir.rglob("*.md"))
    before_sections = _count_sections_for_doc(neo4j_profile, first.vault_uri, target.name)
    target.write_text(target.read_text() + "\n## NEW Section\n\nfresh content.\n")

    second = _run_ingest(vault_dir, neo4j_profile, batch_size=64)
    # Vault-level sync: every doc is "added" on re-index. The edit is visible.
    assert second.docs_added == expected_files
    after_sections = _count_sections_for_doc(neo4j_profile, first.vault_uri, target.name)
    assert after_sections >= before_sections + 1


def test_reindex_drops_stale_docs(tmp_path, neo4j_profile, cleanup_vault):
    """A file removed from disk between ingests vanishes from the graph.

    This is the core motivation for vault-level sync (closes #3) — the
    fileHash-skip incremental model couldn't detect stale docs without an
    extra pass; vault-level sync handles it for free via pre-ingest nuke.
    """
    vault = tmp_path / "stale-drop-vault"
    vault.mkdir()
    (vault / "keep.md").write_text("# Keep\n\nbody.\n")
    (vault / "stale.md").write_text("# Stale\n\nbody.\n")

    first = _run_ingest(vault, neo4j_profile, batch_size=64)
    cleanup_vault.append(first.vault_uri)
    assert first.docs_added == 2

    # Remove one file on disk.
    (vault / "stale.md").unlink()

    second = _run_ingest(vault, neo4j_profile, batch_size=64)
    assert second.docs_added == 1  # only `keep.md`

    # The stale doc is gone from the graph.
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        row = session.run(
            "MATCH (d:Document) WHERE d.uri STARTS WITH $u + '/' RETURN d.name AS name",
            u=first.vault_uri,
        ).data()
        names = {r["name"] for r in row}
    assert names == {"keep.md"}


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

    assert read_vault_uri(fresh) is None  # precondition: no marker

    result = _run_ingest(fresh, neo4j_profile, batch_size=64)
    cleanup_vault.append(result.vault_uri)

    assert result.vault_created is True
    assert read_vault_uri(fresh) == result.vault_uri
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
    old_profile = os.environ.get("KI_PROFILE")
    os.environ["XDG_CONFIG_HOME"] = str(tmp_path)
    # Clear KI_PROFILE so a developer shell with `export KI_PROFILE=...` doesn't
    # override the temp config's default profile (see Config.get_profile).
    os.environ.pop("KI_PROFILE", None)
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
        if old_profile is not None:
            os.environ["KI_PROFILE"] = old_profile

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
    """A second ingest with no filesystem changes lands in the same graph shape.

    With vault-level sync (docs/index_rm_behavior.md), re-index nukes and
    re-creates everything — so we assert the *post-state* is identical
    (same folder count, no duplicates) rather than the "doc was skipped"
    counters that the old fileHash-skip model surfaced.
    """
    fresh = tmp_path / "idempotent-vault"
    fresh.mkdir()
    _build_nasty_vault(fresh)

    first = _run_ingest(fresh, neo4j_profile, batch_size=64)
    cleanup_vault.append(first.vault_uri)
    assert first.folders_total == 10

    second = _run_ingest(fresh, neo4j_profile, batch_size=64)
    assert second.folders_total == 10  # same count post-rebuild
    # Re-index is a full rebuild now: every doc is re-added.
    assert second.docs_added == 11

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

    # Re-ingest with edited content so the resolver re-runs against a fresh
    # graph state (vault-level sync nukes + re-ingests every time).
    (fresh / "index.md").write_text(
        "# Index\n\nSee [[target]] and [[Other]] for details.\n"
    )
    second = _run_ingest(fresh, neo4j_profile, batch_size=64)
    # Vault-level sync: every doc is re-added on re-index.
    assert second.docs_added == 3

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

    fresh = tmp_path / "rm-folders-vault"
    fresh.mkdir()
    _build_nasty_vault(fresh)
    result = _run_ingest(fresh, neo4j_profile, batch_size=64)
    v = result.vault_uri

    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            from ki.ingest.remove import remove_vault
            remove_vault(session, v)

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


def test_path_property_is_set_on_every_node(tmp_path, neo4j_profile, cleanup_vault):
    """After `ki index`, every Folder / Document / Section in the vault carries
    a `path` property pointing at its on-disk location.

    Sections share their owning Document's path (intentional redundancy — see
    docs/data-model.md §Section). Folders point at their on-disk directory.
    Documents point at their on-disk file.
    """
    import os

    fresh = tmp_path / "path-vault"
    fresh.mkdir()
    (fresh / "notes").mkdir()
    (fresh / "notes" / "projects").mkdir()
    (fresh / "root.md").write_text("# Root\n\nbody.\n")
    (fresh / "notes" / "n1.md").write_text("# N1\n\nbody.\n## Sub\n\nmore.\n")
    (fresh / "notes" / "projects" / "p1.md").write_text("# P1\n\nbody.\n")

    result = _run_ingest(fresh, neo4j_profile, batch_size=64)
    cleanup_vault.append(result.vault_uri)
    v = result.vault_uri

    with driver_for(neo4j_profile) as driver, driver.session() as session:
        # Every Folder has a `path` and it exists on disk and is a directory.
        folder_rows = list(
            session.run(
                "MATCH (f:Folder) WHERE f.uri STARTS WITH $u + '/' RETURN f.uri AS uri, f.path AS path",
                u=v,
            )
        )
        assert folder_rows, "expected at least one Folder"
        for r in folder_rows:
            assert r["path"], f"Folder {r['uri']} has no path"
            assert os.path.isdir(r["path"]), f"Folder.path does not exist on disk: {r['path']}"

        # Every Document has a `path` and it points at an existing file.
        doc_rows = list(
            session.run(
                "MATCH (d:Document) WHERE d.uri STARTS WITH $u + '/' RETURN d.uri AS uri, d.path AS path",
                u=v,
            )
        )
        assert len(doc_rows) == 3
        for r in doc_rows:
            assert r["path"], f"Document {r['uri']} has no path"
            assert os.path.isfile(r["path"]), f"Document.path is not a file: {r['path']}"

        # Every Section has a `path` matching its owning Document's path.
        sec_rows = list(
            session.run(
                """
                MATCH (d:Document)-[:HAS*]->(s:Section)
                WHERE d.uri STARTS WITH $u + '/'
                RETURN s.uri AS uri, s.path AS section_path, d.path AS doc_path
                """,
                u=v,
            )
        )
        assert sec_rows, "expected at least one Section"
        for r in sec_rows:
            assert r["section_path"] == r["doc_path"], (
                f"Section.path ({r['section_path']!r}) should equal "
                f"owning Document.path ({r['doc_path']!r})"
            )


def test_path_updates_on_reindex_from_different_mount(tmp_path, neo4j_profile, cleanup_vault):
    """If a vault is re-indexed from a different mount point, every node's
    `path` updates to the new absolute path (last-write-wins, machine-scoped).

    Simulates the Dropbox / iCloud case: same Vault.uri (preserved by the
    marker file), different on-disk location.
    """
    import shutil

    original = tmp_path / "original-mount" / "my-vault"
    original.mkdir(parents=True)
    (original / "foo.md").write_text("# Foo\n\nbody.\n")

    result1 = _run_ingest(original, neo4j_profile, batch_size=64)
    cleanup_vault.append(result1.vault_uri)
    v = result1.vault_uri

    # Move the vault to a new mount (preserves the .ki/vault.yaml marker, so
    # the Vault.uri stays the same when we re-index).
    relocated = tmp_path / "relocated-mount" / "my-vault"
    relocated.parent.mkdir(parents=True)
    shutil.move(str(original), str(relocated))

    result2 = _run_ingest(relocated, neo4j_profile, batch_size=64)
    assert result2.vault_uri == v  # marker file preserved → same vault

    with driver_for(neo4j_profile) as driver, driver.session() as session:
        row = session.run(
            "MATCH (d:Document) WHERE d.uri STARTS WITH $u + '/' RETURN d.path AS path",
            u=v,
        ).single()
        assert row["path"].startswith(str(relocated)), (
            f"Document.path should reflect the new mount point; got {row['path']!r}"
        )
