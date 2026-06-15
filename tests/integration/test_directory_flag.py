"""`-C/--directory` relocates profile resolution for the read commands.

Proof shape: make the config DEFAULT an unreachable profile, but bind the vault
to the real one. Without `-C` the read command would hit the broken default;
with `-C <vault_dir>` it must resolve the vault's bound profile and succeed.
"""

from __future__ import annotations

import pytest

from ki.commands.get import cmd_get
from ki.commands.outline import cmd_outline
from ki.commands.search import cmd_search
from ki.commands.status import cmd_status
from ki.config import Config, Profile, save_config
from ki.ingest.pipeline import IngestOptions, ingest_vault
from ki.neo4j_client import driver_for

pytestmark = pytest.mark.integration


@pytest.fixture
def vault_with_broken_default(vault_dir, neo4j_profile, cleanup_vault, tmp_path, monkeypatch):
    """Index the vault (bound to the real profile); write a config whose DEFAULT
    is an unreachable profile."""
    res = ingest_vault(vault_dir, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("KI_PROFILE", raising=False)
    cfg = Config()
    cfg.add_profile(Profile(name="bogus", uri="bolt://localhost:1", user="x", password="x"))
    cfg.add_profile(Profile(
        name=neo4j_profile.name, uri=neo4j_profile.uri,
        user=neo4j_profile.user, password=neo4j_profile.password,
    ))
    save_config(cfg)
    return vault_dir, res.vault_uri


def test_search_directory_relocates_to_bound_profile(vault_with_broken_default):
    vault_dir, _ = vault_with_broken_default
    rc = cmd_search(
        "the", profile=None, types_csv="document,section",
        k=5, as_json=True, directory=vault_dir,
    )
    assert rc == 0  # resolved the vault's bound profile, not the bogus default


def test_search_without_directory_hits_broken_default(vault_with_broken_default):
    # cwd (repo) isn't a vault and no --profile → search refuses to run
    # (it requires an explicit profile; no default_profile fallback).
    with pytest.raises(Exception):  # noqa: B017 — any error proves it refused
        cmd_search(
            "the", profile=None, types_csv="document",
            k=5, as_json=True, directory=None,
        )


def test_outline_directory_relocates(vault_with_broken_default):
    vault_dir, _ = vault_with_broken_default
    rc = cmd_outline(profile=None, at=None, depth=2, full=False, directory=vault_dir)
    assert rc == 0


def test_outline_bare_renders_the_vault_youre_in(vault_with_broken_default, capsys):
    """Bare `ki outline` (no uri, no --profile) scopes to the vault at -C/cwd,
    not all vaults — its uri shows up as the rendered root."""
    vault_dir, vault_uri = vault_with_broken_default
    rc = cmd_outline(profile=None, at=None, depth=2, full=False, directory=vault_dir)
    assert rc == 0
    out = capsys.readouterr().out
    assert vault_uri in out


def test_status_directory_relocates(vault_with_broken_default):
    vault_dir, _ = vault_with_broken_default
    # -C resolves the vault's bound profile (not the bogus default); freshly
    # indexed → READY → exit 0.
    rc = cmd_status(vault_dir, profile=None, as_json=True, verbose=False)
    assert rc == 0


def test_get_directory_relocates(vault_with_broken_default, neo4j_profile):
    vault_dir, vault_uri = vault_with_broken_default
    with driver_for(neo4j_profile) as d, d.session() as s:
        row = s.run(
            "MATCH (x:Document) WHERE x.sourceType = 'LOCAL_FILE' "
            "AND x.uri STARTS WITH $p RETURN x.uri AS u LIMIT 1",
            p=vault_uri + "/",
        ).single()
    doc_uri = row["u"]
    rc = cmd_get(
        (doc_uri,), profile=None, get_type="path",
        as_json=True, directory=vault_dir,
    )
    assert rc == 0
