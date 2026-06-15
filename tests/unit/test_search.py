"""Unit tests for `ki search` — pure-Python pieces of `ki.commands.search`.

Covers --types CSV parsing, profile+vault scope resolution, run_search param
plumbing, and the table-render header. Integration tests against an ephemeral
Neo4j live in tests/integration/test_search.py.
"""

from __future__ import annotations

import pytest
from click import ClickException
from click.testing import CliRunner

from ki.cli import main
from ki.commands.search import (
    DEFAULT_TYPES,
    TYPE_LETTER,
    VALID_TYPES,
    _parse_types,
    _resolve_scope,
    resolve_to_uri,
)
from ki.config import Config, Profile
from ki.search.queries import run_search
from ki.vault import write_vault_marker

# ---- _parse_types ----------------------------------------------------------


def test_parse_types_default_is_both():
    assert _parse_types(DEFAULT_TYPES) == list(VALID_TYPES)


def test_parse_types_single():
    assert _parse_types("section") == ["section"]


def test_parse_types_csv_preserves_canonical_order():
    assert _parse_types("section,document") == ["document", "section"]


def test_parse_types_strips_whitespace_and_lowercases():
    assert _parse_types("  SECTION ,Document  ") == ["document", "section"]


def test_parse_types_dedupes():
    assert _parse_types("section,section,document") == ["document", "section"]


def test_parse_types_empty_string_errors():
    with pytest.raises(ClickException):
        _parse_types("")


def test_parse_types_unknown_value_errors():
    with pytest.raises(ClickException) as excinfo:
        _parse_types("section,bogus")
    assert "bogus" in str(excinfo.value)


def test_parse_types_rejects_vault_now():
    with pytest.raises(ClickException):
        _parse_types("vault")


# ---- _resolve_scope --------------------------------------------------------


def _cfg() -> Config:
    return Config(
        profiles={
            "personal": Profile("personal", "bolt://h:7687", "neo4j", "pw", "local-podman"),
            "work": Profile("work", "bolt://h:7688", "neo4j", "pw", "local-podman"),
        },
        default_profile="personal",
    )


def _vault_dir(tmp_path, *, uri="my-notes", profile="personal"):
    write_vault_marker(tmp_path, uri=uri, profile=profile)
    return tmp_path


def _scope(cfg, *, profile_flag=None, under_flag=None, vault_flag=None, start_dir):
    return _resolve_scope(
        cfg, profile_flag=profile_flag, under_flag=under_flag,
        vault_flag=vault_flag, start_dir=start_dir,
    )


# -- flag-combination guards --

def test_resolve_requires_a_profile_outside_a_vault(tmp_path):
    with pytest.raises(ClickException) as e:
        _scope(_cfg(), start_dir=tmp_path)
    assert "profile" in str(e.value).lower()


def test_resolve_vault_without_profile_errors(tmp_path):
    with pytest.raises(ClickException) as e:
        _scope(_cfg(), vault_flag="notes", start_dir=tmp_path)
    assert "--vault requires --profile" in str(e.value)


def test_resolve_under_and_vault_mutually_exclusive(tmp_path):
    with pytest.raises(ClickException) as e:
        _scope(
            _cfg(), profile_flag="work", under_flag="notes/x",
            vault_flag="notes", start_dir=tmp_path,
        )
    assert "mutually exclusive" in str(e.value)


# -- remote mode (--profile) --

def test_resolve_profile_flag_means_all_vaults(tmp_path):
    prof, scope, banner = _scope(_cfg(), profile_flag="work", start_dir=tmp_path)
    assert prof.name == "work"
    assert scope is None
    assert "all vaults" in banner


def test_resolve_profile_and_one_vault(tmp_path):
    prof, scope, banner = _scope(
        _cfg(), profile_flag="work", vault_flag="notes", start_dir=tmp_path
    )
    assert prof.name == "work"
    assert scope == ["notes"]
    assert "notes" in banner


def test_resolve_profile_and_vault_csv(tmp_path):
    _, scope, _ = _scope(
        _cfg(), profile_flag="work", vault_flag="notes, docs/", start_dir=tmp_path
    )
    assert scope == ["notes", "docs"]  # split, trimmed, trailing slash stripped


def test_resolve_empty_vault_csv_errors(tmp_path):
    with pytest.raises(ClickException) as e:
        _scope(_cfg(), profile_flag="work", vault_flag=" , ", start_dir=tmp_path)
    assert "empty" in str(e.value).lower()


def test_resolve_remote_under_uri(tmp_path):
    prof, scope, banner = _scope(
        _cfg(), profile_flag="work", under_flag="work-notes/api/v2", start_dir=tmp_path
    )
    assert prof.name == "work"
    assert scope == ["work-notes/api/v2"]  # taken verbatim, no local resolution
    assert "under 'work-notes/api/v2'" in banner


def test_resolve_remote_under_path_errors(tmp_path):
    # No local vault to resolve a path against → must be a uri.
    with pytest.raises(ClickException) as e:
        _scope(_cfg(), profile_flag="work", under_flag="./api", start_dir=tmp_path)
    assert "path" in str(e.value).lower()


def test_resolve_unknown_profile_errors(tmp_path):
    with pytest.raises(ClickException):
        _scope(_cfg(), profile_flag="nope", start_dir=tmp_path)


# -- local mode (in a vault) --

def test_resolve_in_vault_default_scopes_to_that_vault(tmp_path):
    vd = _vault_dir(tmp_path)
    prof, scope, banner = _scope(_cfg(), start_dir=vd)
    assert prof.name == "personal"
    assert scope == ["my-notes"]
    assert "vault 'my-notes'" in banner


def test_resolve_under_uri_in_vault(tmp_path):
    vd = _vault_dir(tmp_path)
    prof, scope, banner = _scope(
        _cfg(), under_flag="my-notes/projects", start_dir=vd
    )
    assert prof.name == "personal"
    assert scope == ["my-notes/projects"]
    assert "under 'my-notes/projects'" in banner


def test_resolve_under_path_in_vault(tmp_path):
    vd = _vault_dir(tmp_path)
    (vd / "projects").mkdir()
    _, scope, _ = _scope(_cfg(), under_flag="./projects", start_dir=vd)
    assert scope == ["my-notes/projects"]


def test_resolve_bound_profile_missing_from_config_errors(tmp_path):
    vd = _vault_dir(tmp_path, profile="ghost")  # not in cfg
    with pytest.raises(ClickException) as e:
        _scope(_cfg(), start_dir=vd)
    assert "ghost" in str(e.value)


# ---- resolve_to_uri --------------------------------------------------------


def test_resolve_to_uri_uri_in_vault_passthrough(tmp_path):
    assert resolve_to_uri("my-notes/foo.md", "my-notes", tmp_path, cwd=tmp_path) \
        == "my-notes/foo.md"
    # the vault uri itself, and a trailing slash, both normalize
    assert resolve_to_uri("my-notes", "my-notes", tmp_path, cwd=tmp_path) == "my-notes"
    assert resolve_to_uri("my-notes/a/", "my-notes", tmp_path, cwd=tmp_path) == "my-notes/a"


def test_resolve_to_uri_existing_dir_to_uri(tmp_path):
    (tmp_path / "My Projects").mkdir()
    assert resolve_to_uri("./My Projects", "my-notes", tmp_path, cwd=tmp_path) \
        == "my-notes/my-projects"  # slugified


def test_resolve_to_uri_vault_root_path(tmp_path):
    assert resolve_to_uri(".", "my-notes", tmp_path, cwd=tmp_path) == "my-notes"


def test_resolve_to_uri_nonexistent_non_uri_errors(tmp_path):
    with pytest.raises(ClickException):
        resolve_to_uri("nope/missing", "my-notes", tmp_path, cwd=tmp_path)


# ---- run_search param plumbing ---------------------------------------------


class _FakeSession:
    def __init__(self):
        self.params = None

    def run(self, _query, parameters=None):
        self.params = parameters
        return []


def test_run_search_threads_scope_labels_k():
    s = _FakeSession()
    run_search(s, "q", scope_uris=["my-notes"], labels=["Section"], k=7)
    assert s.params["scope"] == ["my-notes"]
    assert s.params["labels"] == ["Section"]
    assert s.params["k"] == 7


# ---- render ----------------------------------------------------------------


def test_type_letter_mapping():
    assert TYPE_LETTER == {"Document": "D", "Section": "S"}


# ---- CLI surface -----------------------------------------------------------


def test_search_help_shows_types_flag():
    res = CliRunner().invoke(main, ["search", "--help"])
    assert res.exit_code == 0
    assert "--types" in res.output
    for t in VALID_TYPES:
        assert t in res.output


def test_search_help_has_vault_and_profile_flags():
    res = CliRunner().invoke(main, ["search", "--help"])
    assert "--vault" in res.output
    assert "--profile" in res.output


def test_search_help_no_longer_has_all_flag():
    res = CliRunner().invoke(main, ["search", "--help"])
    assert "--all" not in res.output


def test_search_help_no_singular_type_flag():
    res = CliRunner().invoke(main, ["search", "--help"])
    assert "--types" in res.output
    assert "--type " not in res.output


def test_search_rejects_bogus_types_value():
    res = CliRunner().invoke(main, ["search", "foo", "--types", "bogus"])
    assert res.exit_code != 0
    assert "bogus" in res.output


def test_search_help_documents_default_types():
    res = CliRunner().invoke(main, ["search", "--help"])
    assert res.exit_code == 0
    assert DEFAULT_TYPES in res.output
