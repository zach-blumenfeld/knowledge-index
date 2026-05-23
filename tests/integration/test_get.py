"""End-to-end tests for `ki get` against an ephemeral Neo4j.

Indexes a small synthetic vault per test so URIs are predictable.
"""

from __future__ import annotations

import pytest

from ki.ingest.pipeline import IngestOptions, ingest_vault
from ki.neo4j_client import driver_for
from ki.search.queries import run_b4, run_b13, run_b14

pytestmark = pytest.mark.integration


@pytest.fixture
def ingested_doc_vault(tmp_path, neo4j_profile, cleanup_vault):
    """Index a 1-doc vault with deterministic content and return (vault_uri, doc_uri)."""
    vault = tmp_path / "get-vault"
    vault.mkdir()
    (vault / "essay.md").write_text(
        "preamble paragraph before any heading.\n\n"
        "# Big\n\n"
        "h1 body text.\n\n"
        "## Background\n\n"
        "background body.\n\n"
        "## Approach\n\n"
        "approach body.\n"
    )
    res = ingest_vault(vault, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)
    doc_uri = f"{res.vault_uri}/essay.md"
    return res.vault_uri, doc_uri


# ---- B.13 -----------------------------------------------------------------


def test_b13_returns_document_metadata(ingested_doc_vault, neo4j_profile):
    _vault_uri, doc_uri = ingested_doc_vault
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        row = run_b13(session, doc_uri)
    assert row is not None
    assert row["label"] == "Document"
    assert row["uri"] == doc_uri
    assert row["path"].endswith("essay.md")
    assert row["sourceType"] == "LOCAL_FILE"
    # `headingLevel` is a Section property — B.13 returns properties(n), so
    # absent keys are simply not in the dict (None via .get()).
    assert row.get("headingLevel") is None


def test_b13_returns_section_metadata(ingested_doc_vault, neo4j_profile):
    _vault_uri, doc_uri = ingested_doc_vault
    section_uri = f"{doc_uri}#big"
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        row = run_b13(session, section_uri)
    assert row is not None
    assert row["label"] == "Section"
    assert row["headingLevel"] == 1
    assert row["path"] is not None  # path is on the parent doc; also on sections (per #40).
    # Doc-only fields are absent from the Section properties bag.
    assert row.get("sourceType") is None
    assert row.get("frontmatter") is None


def test_b13_returns_folder_label_so_dispatcher_can_reject(
    ingested_doc_vault, neo4j_profile, tmp_path, cleanup_vault,
):
    """B.13 must return label=Folder so get.py can route it to the error path."""
    # Make a folder by ingesting a doc nested under a directory.
    vault = tmp_path / "folder-vault"
    vault.mkdir()
    (vault / "nested").mkdir()
    (vault / "nested" / "doc.md").write_text("# D\n\nbody.\n")
    res = ingest_vault(vault, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)

    folder_uri = f"{res.vault_uri}/nested"
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        row = run_b13(session, folder_uri)
    assert row is not None
    assert row["label"] == "Folder"


def test_b13_returns_vault_label(ingested_doc_vault, neo4j_profile):
    vault_uri, _doc_uri = ingested_doc_vault
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        row = run_b13(session, vault_uri)
    assert row is not None
    assert row["label"] == "Vault"


def test_b13_missing_uri_returns_none(neo4j_profile):
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        row = run_b13(session, "vault://does-not-exist/anywhere.md")
    assert row is None


# ---- B.4 (lifted into queries.py for `ki get --type full` on docs) --------


def test_b4_returns_sections_in_reading_order(ingested_doc_vault, neo4j_profile):
    _vault_uri, doc_uri = ingested_doc_vault
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        rows = run_b4(session, doc_uri)
    headings = [r["heading"] for r in rows]
    # Reading order: H1 'Big' first, then H2 'Background', then H2 'Approach'.
    assert headings == ["Big", "Background", "Approach"]
    # And each row has a content body.
    for r in rows:
        assert r["content"] is not None


# ---- B.14 (section + subtree) ---------------------------------------------


def test_b14_section_with_subtree_returns_descendants(ingested_doc_vault, neo4j_profile):
    """Calling B.14 on the H1 should return H1 + both H2 descendants in reading order."""
    _vault_uri, doc_uri = ingested_doc_vault
    h1_uri = f"{doc_uri}#big"
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        rows = run_b14(session, h1_uri)
    headings = [r["heading"] for r in rows]
    assert headings == ["Big", "Background", "Approach"]


def test_b14_leaf_section_returns_just_itself(ingested_doc_vault, neo4j_profile):
    """An H2 with no children returns a single row — itself."""
    _vault_uri, doc_uri = ingested_doc_vault
    h2_uri = f"{doc_uri}#big/background"
    with driver_for(neo4j_profile) as driver, driver.session() as session:
        rows = run_b14(session, h2_uri)
    headings = [r["heading"] for r in rows]
    assert headings == ["Background"]


# ---- cmd_get end-to-end ---------------------------------------------------


def _write_test_config(tmp_path, neo4j_profile, monkeypatch):
    """Materialize the active neo4j_profile under $XDG_CONFIG_HOME/ki/config.yaml."""
    import yaml

    xdg = tmp_path / "xdg"
    (xdg / "ki").mkdir(parents=True)
    (xdg / "ki" / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "default_profile": neo4j_profile.name,
                "profiles": {
                    neo4j_profile.name: {
                        "uri": neo4j_profile.uri,
                        "user": neo4j_profile.user,
                        "password": neo4j_profile.password,
                    }
                },
            }
        )
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    # Clear KI_PROFILE so a developer shell with `export KI_PROFILE=...` doesn't
    # override the temp config's default_profile and break test isolation. See
    # Config.get_profile's resolution order: arg → KI_PROFILE → default_profile.
    monkeypatch.delenv("KI_PROFILE", raising=False)


def test_cmd_get_content_returns_node_content(
    ingested_doc_vault, neo4j_profile, tmp_path, monkeypatch, capsys,
):
    from ki.commands.get import cmd_get

    _write_test_config(tmp_path, neo4j_profile, monkeypatch)
    _vault_uri, doc_uri = ingested_doc_vault
    rc = cmd_get((doc_uri,), profile=None, get_type="content", as_json=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert doc_uri in out
    assert "label: Document" in out


def test_cmd_get_full_returns_reconstructed_doc(
    ingested_doc_vault, neo4j_profile, tmp_path, monkeypatch, capsys,
):
    from ki.commands.get import cmd_get

    _write_test_config(tmp_path, neo4j_profile, monkeypatch)
    _vault_uri, doc_uri = ingested_doc_vault
    rc = cmd_get((doc_uri,), profile=None, get_type="full", as_json=False)
    assert rc == 0
    out = capsys.readouterr().out
    # The reconstructed body should contain all three headings + bodies.
    assert "# Big" in out
    assert "## Background" in out
    assert "## Approach" in out
    assert "h1 body text." in out
    assert "background body." in out
    assert "approach body." in out


def test_cmd_get_full_section_returns_subtree(
    ingested_doc_vault, neo4j_profile, tmp_path, monkeypatch, capsys,
):
    from ki.commands.get import cmd_get

    _write_test_config(tmp_path, neo4j_profile, monkeypatch)
    _vault_uri, doc_uri = ingested_doc_vault
    h1_uri = f"{doc_uri}#big"
    rc = cmd_get((h1_uri,), profile=None, get_type="full", as_json=False)
    assert rc == 0
    out = capsys.readouterr().out
    # H1 subtree: should include the H1 heading + both H2 subsections.
    assert "# Big" in out
    assert "## Background" in out
    assert "## Approach" in out


def test_cmd_get_path_type_omits_content(
    ingested_doc_vault, neo4j_profile, tmp_path, monkeypatch, capsys,
):
    from ki.commands.get import cmd_get

    _write_test_config(tmp_path, neo4j_profile, monkeypatch)
    _vault_uri, doc_uri = ingested_doc_vault
    rc = cmd_get((doc_uri,), profile=None, get_type="path", as_json=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "no content emitted" in out
    assert "h1 body text." not in out


def test_cmd_get_rejects_folder_uri_with_helpful_error(
    neo4j_profile, tmp_path, monkeypatch, cleanup_vault, capsys,
):
    from ki.commands.get import cmd_get

    vault = tmp_path / "folder-vault"
    vault.mkdir()
    (vault / "nested").mkdir()
    (vault / "nested" / "d.md").write_text("# D\n\nbody.\n")
    res = ingest_vault(vault, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)
    folder_uri = f"{res.vault_uri}/nested"

    _write_test_config(tmp_path, neo4j_profile, monkeypatch)
    rc = cmd_get((folder_uri,), profile=None, get_type="content", as_json=False)
    err = capsys.readouterr().err
    assert rc == 1
    assert "Folder" in err
    assert "ki outline " in err


def test_cmd_get_rejects_vault_uri_with_helpful_error(
    ingested_doc_vault, neo4j_profile, tmp_path, monkeypatch, capsys,
):
    from ki.commands.get import cmd_get

    _write_test_config(tmp_path, neo4j_profile, monkeypatch)
    vault_uri, _doc_uri = ingested_doc_vault
    rc = cmd_get((vault_uri,), profile=None, get_type="content", as_json=False)
    err = capsys.readouterr().err
    assert rc == 1
    assert "Vault" in err
    assert "ki vault list" in err
    assert "ki outline " in err


def test_cmd_get_missing_uri_emits_clean_not_found(
    ingested_doc_vault, neo4j_profile, tmp_path, monkeypatch, capsys,
):
    from ki.commands.get import cmd_get

    _write_test_config(tmp_path, neo4j_profile, monkeypatch)
    rc = cmd_get(
        ("vault://does-not-exist/missing.md",),
        profile=None,
        get_type="content",
        as_json=False,
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "no node found for uri" in err
    assert "vault://does-not-exist/missing.md" in err


def test_cmd_get_batch_mixes_valid_and_invalid(
    ingested_doc_vault, neo4j_profile, tmp_path, monkeypatch, capsys,
):
    """A batch with one valid + one missing URI: valid renders, missing errors, rc=1."""
    from ki.commands.get import cmd_get

    _write_test_config(tmp_path, neo4j_profile, monkeypatch)
    _vault_uri, doc_uri = ingested_doc_vault
    rc = cmd_get(
        (doc_uri, "vault://missing/x.md"),
        profile=None,
        get_type="content",
        as_json=False,
    )
    captured = capsys.readouterr()
    assert rc == 1
    assert doc_uri in captured.out
    assert "no node found" in captured.err


def test_cmd_get_json_payload_includes_path(
    ingested_doc_vault, neo4j_profile, tmp_path, monkeypatch, capsys,
):
    """--json must surface the `path` property on every result (per #40)."""
    import json

    from ki.commands.get import cmd_get

    _write_test_config(tmp_path, neo4j_profile, monkeypatch)
    _vault_uri, doc_uri = ingested_doc_vault
    rc = cmd_get((doc_uri,), profile=None, get_type="content", as_json=True)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["type"] == "content"
    assert len(payload["results"]) == 1
    result = payload["results"][0]
    assert result["uri"] == doc_uri
    assert result["path"] is not None
    assert result["path"].endswith("essay.md")
