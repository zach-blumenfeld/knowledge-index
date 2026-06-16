"""Integration tests for `ki rm` — subtree removal (document / folder).

`ki rm` is the incremental sibling of `ki drop`: it removes one Document or
Folder (and its subtree) from the index, source untouched. See
`src/ki/commands/rm.py` and `docs/data-model/index_rm_behavior.md`.
"""

from __future__ import annotations

import json

import click
import pytest

from ki.commands.rm import cmd_rm
from ki.config import Config, Profile, save_config
from ki.ingest.pipeline import IngestOptions, ingest_vault
from ki.neo4j_client import driver_for
from ki.scope import resolve_to_uri

pytestmark = pytest.mark.integration


@pytest.fixture
def indexed_vault(vault_dir, neo4j_profile, cleanup_vault, monkeypatch, tmp_path):
    """Index the sample vault and write a Config so cmd_rm can resolve a profile.

    The sample marker carries only `uri:` (no profile binding), so local-mode
    resolution falls through to `$KI_PROFILE` — the documented last resort.
    """
    res = ingest_vault(vault_dir, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("KI_PROFILE", neo4j_profile.name)
    cfg = Config()
    cfg.add_profile(Profile(
        name=neo4j_profile.name, uri=neo4j_profile.uri,
        user=neo4j_profile.user, password=neo4j_profile.password,
    ))
    save_config(cfg)
    return res.vault_uri


def _count_under(profile, uri_prefix_root):
    """Count nodes in the subtree rooted at `uri_prefix_root` (3-part containment)."""
    with driver_for(profile) as driver, driver.session() as session:
        row = session.run(
            "MATCH (n) WHERE n.uri = $r OR n.uri STARTS WITH $r + '/' "
            "OR n.uri STARTS WITH $r + '#' RETURN count(n) AS n",
            r=uri_prefix_root,
        ).single()
        return row["n"] if row else 0


def _exists(profile, uri):
    with driver_for(profile) as driver, driver.session() as session:
        row = session.run(
            "MATCH (n {uri: $u}) RETURN count(n) AS n", u=uri
        ).single()
        return bool(row and row["n"])


def _a_section_uri(profile, vault_uri):
    with driver_for(profile) as driver, driver.session() as session:
        row = session.run(
            "MATCH (s:Section) WHERE s.uri STARTS WITH $v + '/' RETURN s.uri AS u LIMIT 1",
            v=vault_uri,
        ).single()
        return row["u"] if row else None


# ---- Document removal ------------------------------------------------------


def test_rm_document_by_path_removes_doc_and_sections_keeps_siblings(
    indexed_vault, vault_dir, neo4j_profile
):
    doc_uri = resolve_to_uri(
        "concepts/duplicate-headings.md", indexed_vault, vault_dir, cwd=vault_dir
    )
    sibling_uri = resolve_to_uri(
        "concepts/heading-skip.md", indexed_vault, vault_dir, cwd=vault_dir
    )
    assert _exists(neo4j_profile, doc_uri)
    assert _count_under(neo4j_profile, doc_uri) >= 1  # doc + its sections

    rc = cmd_rm("concepts/duplicate-headings.md", directory=vault_dir)
    assert rc == 0

    # Doc + its entire `#`-section subtree gone.
    assert _count_under(neo4j_profile, doc_uri) == 0
    # Sibling document and the vault itself untouched.
    assert _exists(neo4j_profile, sibling_uri)
    assert _exists(neo4j_profile, indexed_vault)


def test_rm_document_source_file_untouched(indexed_vault, vault_dir, neo4j_profile):
    src = vault_dir / "concepts" / "duplicate-headings.md"
    assert src.exists()
    cmd_rm("concepts/duplicate-headings.md", directory=vault_dir)
    assert src.exists()  # only the index entry is gone


# ---- Folder removal (incl. nested) -----------------------------------------


def test_rm_folder_by_path_removes_whole_subtree(indexed_vault, vault_dir, neo4j_profile):
    folder_uri = resolve_to_uri("science", indexed_vault, vault_dir, cwd=vault_dir)
    nested_doc_uri = resolve_to_uri(
        "science/i/in-practice-macos.md", indexed_vault, vault_dir, cwd=vault_dir
    )
    other_folder_uri = resolve_to_uri("inbox", indexed_vault, vault_dir, cwd=vault_dir)
    assert _exists(neo4j_profile, nested_doc_uri)

    rc = cmd_rm("science", directory=vault_dir)
    assert rc == 0

    # Folder + nested subfolder + nested doc all gone.
    assert _count_under(neo4j_profile, folder_uri) == 0
    # A different folder is untouched.
    assert _count_under(neo4j_profile, other_folder_uri) > 0
    assert _exists(neo4j_profile, indexed_vault)


# ---- Guards ----------------------------------------------------------------


def test_rm_vault_uri_errors_pointing_at_drop(indexed_vault, neo4j_profile):
    before = _count_under(neo4j_profile, indexed_vault)
    with pytest.raises(click.ClickException) as exc:
        cmd_rm(indexed_vault, profile=neo4j_profile.name)
    assert "ki drop" in exc.value.message
    assert _count_under(neo4j_profile, indexed_vault) == before  # no changes


def test_rm_section_uri_errors(indexed_vault, neo4j_profile):
    section_uri = _a_section_uri(neo4j_profile, indexed_vault)
    assert section_uri is not None
    before = _exists(neo4j_profile, section_uri)
    with pytest.raises(click.ClickException) as exc:
        cmd_rm(section_uri, profile=neo4j_profile.name)
    assert "section" in exc.value.message.lower()
    assert _exists(neo4j_profile, section_uri) == before  # no changes


def test_rm_unknown_in_namespace_uri_errors(indexed_vault, neo4j_profile):
    ghost = f"{indexed_vault}/concepts/does-not-exist.md"
    with pytest.raises(click.ClickException) as exc:
        cmd_rm(ghost, profile=neo4j_profile.name)
    assert "nothing indexed" in exc.value.message.lower()


# ---- Dry-run / JSON --------------------------------------------------------


def test_rm_dry_run_makes_no_changes(indexed_vault, vault_dir, neo4j_profile):
    doc_uri = resolve_to_uri(
        "concepts/duplicate-headings.md", indexed_vault, vault_dir, cwd=vault_dir
    )
    before = _count_under(neo4j_profile, doc_uri)
    assert before >= 1
    rc = cmd_rm("concepts/duplicate-headings.md", directory=vault_dir, dry_run=True)
    assert rc == 0
    assert _count_under(neo4j_profile, doc_uri) == before  # untouched


def test_rm_json_output(indexed_vault, vault_dir, neo4j_profile, capsys):
    rc = cmd_rm("concepts/duplicate-headings.md", directory=vault_dir, as_json=True)
    assert rc == 0
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["label"] == "Document"
    assert payload["nodes_removed"] >= 1
    assert payload["uri"].endswith("concepts/duplicate-headings.md")


# ---- Remote (`--profile`) mode --------------------------------------------


def test_rm_remote_by_uri(indexed_vault, vault_dir, neo4j_profile):
    """--profile + uri removes without needing a local vault."""
    doc_uri = resolve_to_uri(
        "concepts/heading-skip.md", indexed_vault, vault_dir, cwd=vault_dir
    )
    assert _exists(neo4j_profile, doc_uri)
    rc = cmd_rm(doc_uri, profile=neo4j_profile.name)
    assert rc == 0
    assert _exists(neo4j_profile, doc_uri) is False


def test_rm_remote_rejects_path(indexed_vault, neo4j_profile):
    with pytest.raises(click.ClickException) as exc:
        cmd_rm("./concepts/heading-skip.md", profile=neo4j_profile.name)
    assert "path" in exc.value.message.lower()


# ---- --chunk-size passthrough ---------------------------------------------


def test_rm_with_chunk_size(indexed_vault, vault_dir, neo4j_profile):
    rc = cmd_rm("inbox", directory=vault_dir, chunk_size=128)
    assert rc == 0
    folder_uri = resolve_to_uri("inbox", indexed_vault, vault_dir, cwd=vault_dir)
    assert _count_under(neo4j_profile, folder_uri) == 0
