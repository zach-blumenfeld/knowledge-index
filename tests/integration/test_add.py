"""Integration tests for `ki add` — incremental subtree (re)index into an
existing vault, built as remove_subtree + ingest_subtree.

Covers the headline behaviors: new doc, edited doc (stale sections cleared),
new folder, outbound links resolving against the whole vault, and the
edge-restore of still-valid INBOUND links across the re-ingest.
"""

from __future__ import annotations

import json

import click
import pytest

from ki.commands.add import cmd_add
from ki.config import Config, Profile, save_config
from ki.ingest.pipeline import IngestOptions, ingest_vault
from ki.neo4j_client import driver_for
from ki.vault import document_uri

pytestmark = pytest.mark.integration


@pytest.fixture
def linked_vault(tmp_path, neo4j_profile, cleanup_vault, monkeypatch):
    """Tiny indexed vault where b.md links [[a]]. Config written for cmd_add."""
    vault = tmp_path / "kb"
    vault.mkdir()
    (vault / "a.md").write_text(
        "# A\n\nAlpha body.\n\n## Sec One\n\ns1\n\n## Sec Two\n\ns2\n"
    )
    (vault / "b.md").write_text("Points to [[a]].\n\n# B\n\nBeta body.\n")
    res = ingest_vault(vault, IngestOptions(profile=neo4j_profile, batch_size=64))
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
    return vault, res.vault_uri


def _exists(profile, uri):
    with driver_for(profile) as driver, driver.session() as session:
        row = session.run("MATCH (n {uri: $u}) RETURN count(n) AS n", u=uri).single()
        return bool(row and row["n"])


def _link_exists(profile, src_uri, tgt_uri):
    with driver_for(profile) as driver, driver.session() as session:
        row = session.run(
            "MATCH ({uri: $s})-[:LINKS_TO]->({uri: $t}) RETURN count(*) AS n",
            s=src_uri, t=tgt_uri,
        ).single()
        return bool(row and row["n"])


# ---- New document ----------------------------------------------------------


def test_add_new_document(linked_vault, neo4j_profile):
    vault, vault_uri = linked_vault
    (vault / "c.md").write_text("See [[a]].\n\n# C\n\nGamma.\n")
    c_uri = document_uri(vault_uri, "c.md")
    a_uri = document_uri(vault_uri, "a.md")
    assert not _exists(neo4j_profile, c_uri)

    rc = cmd_add("c.md", directory=vault)
    assert rc == 0
    assert _exists(neo4j_profile, c_uri)
    # Outbound link resolves against the whole vault (a.md already indexed).
    assert _link_exists(neo4j_profile, c_uri, a_uri)


# ---- Edited document: stale sections cleared, inbound links restored -------


def test_add_edited_document_clears_stale_sections(linked_vault, neo4j_profile):
    vault, vault_uri = linked_vault
    # Sections nest under the H1 ("A"), so the path is `#a/sec-two`.
    sec_two = f"{document_uri(vault_uri, 'a.md')}#a/sec-two"
    assert _exists(neo4j_profile, sec_two)

    # Rewrite a.md to drop "Sec Two".
    (vault / "a.md").write_text("# A\n\nAlpha body.\n\n## Sec One\n\ns1\n")
    rc = cmd_add("a.md", directory=vault)
    assert rc == 0
    assert not _exists(neo4j_profile, sec_two)  # stale section gone
    assert _exists(neo4j_profile, f"{document_uri(vault_uri, 'a.md')}#a/sec-one")


def test_add_edited_document_restores_inbound_links(linked_vault, neo4j_profile):
    """The headline edge-restore: b.md still says [[a]] and a.md still exists,
    so b->a must survive a `ki add a.md` (matching a full ki index)."""
    vault, vault_uri = linked_vault
    a_uri = document_uri(vault_uri, "a.md")
    b_uri = document_uri(vault_uri, "b.md")
    assert _link_exists(neo4j_profile, b_uri, a_uri)  # before

    # Edit a.md (content change only — same location).
    (vault / "a.md").write_text("# A\n\nAlpha body, revised.\n\n## Sec One\n\ns1\n")
    rc = cmd_add("a.md", directory=vault)
    assert rc == 0

    # b->a preserved even though we only re-ingested a.md.
    assert _link_exists(neo4j_profile, b_uri, a_uri)


# ---- New folder ------------------------------------------------------------


def test_add_new_folder_subtree(linked_vault, neo4j_profile):
    vault, vault_uri = linked_vault
    notes = vault / "notes"
    notes.mkdir()
    (notes / "n1.md").write_text("# N1\n\none\n")
    (notes / "n2.md").write_text("# N2\n\ntwo\n")

    rc = cmd_add("notes", directory=vault)
    assert rc == 0
    assert _exists(neo4j_profile, document_uri(vault_uri, "notes/n1.md"))
    assert _exists(neo4j_profile, document_uri(vault_uri, "notes/n2.md"))
    assert _exists(neo4j_profile, f"{vault_uri}/notes")  # folder node


# ---- Guards ----------------------------------------------------------------


def test_add_vault_root_errors_pointing_at_index(linked_vault, neo4j_profile):
    vault, _ = linked_vault
    with pytest.raises(click.ClickException) as exc:
        cmd_add(".", directory=vault)
    assert "ki index" in exc.value.message


def test_add_path_outside_vault_errors(linked_vault, neo4j_profile, tmp_path):
    vault, _ = linked_vault
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n\nx\n")
    with pytest.raises(click.ClickException) as exc:
        cmd_add(str(outside), directory=vault)
    assert "outside the vault" in exc.value.message


def test_add_non_markdown_file_errors(linked_vault, neo4j_profile):
    vault, _ = linked_vault
    (vault / "note.txt").write_text("not markdown\n")
    with pytest.raises(click.ClickException) as exc:
        cmd_add("note.txt", directory=vault)
    assert "markdown" in exc.value.message.lower()


def test_add_nonexistent_path_errors(linked_vault, neo4j_profile):
    vault, _ = linked_vault
    with pytest.raises(click.ClickException) as exc:
        cmd_add("ghost.md", directory=vault)
    assert "nothing at" in exc.value.message.lower()


# ---- Dry-run / JSON --------------------------------------------------------


def test_add_dry_run_makes_no_changes(linked_vault, neo4j_profile):
    vault, vault_uri = linked_vault
    (vault / "c.md").write_text("# C\n\ngamma\n")
    c_uri = document_uri(vault_uri, "c.md")
    rc = cmd_add("c.md", directory=vault, dry_run=True)
    assert rc == 0
    assert not _exists(neo4j_profile, c_uri)  # nothing written


def test_add_json_output(linked_vault, neo4j_profile, capsys):
    vault, vault_uri = linked_vault
    (vault / "c.md").write_text("# C\n\ngamma\n")
    rc = cmd_add("c.md", directory=vault, as_json=True)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["docs_added"] == 1
    assert payload["uri"] == document_uri(vault_uri, "c.md")
