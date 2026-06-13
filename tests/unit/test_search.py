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


def test_resolve_requires_a_profile_outside_a_vault(tmp_path):
    with pytest.raises(ClickException) as e:
        _resolve_scope(_cfg(), profile_flag=None, vault_flag=None, start_dir=tmp_path)
    assert "profile" in str(e.value).lower()


def test_resolve_profile_flag_means_all_vaults(tmp_path):
    prof, prefix, banner = _resolve_scope(
        _cfg(), profile_flag="work", vault_flag=None, start_dir=tmp_path
    )
    assert prof.name == "work"
    assert prefix is None
    assert "all vaults" in banner


def test_resolve_profile_and_vault(tmp_path):
    prof, prefix, _ = _resolve_scope(
        _cfg(), profile_flag="work", vault_flag="notes", start_dir=tmp_path
    )
    assert prof.name == "work"
    assert prefix == "notes/"


def test_resolve_vault_flag_trailing_slash_idempotent(tmp_path):
    _, prefix, _ = _resolve_scope(
        _cfg(), profile_flag="work", vault_flag="notes/", start_dir=tmp_path
    )
    assert prefix == "notes/"


def test_resolve_unknown_profile_errors(tmp_path):
    with pytest.raises(ClickException):
        _resolve_scope(_cfg(), profile_flag="nope", vault_flag=None, start_dir=tmp_path)


def test_resolve_in_vault_default_uses_bound_profile_and_vault(tmp_path):
    vd = _vault_dir(tmp_path)
    prof, prefix, banner = _resolve_scope(
        _cfg(), profile_flag=None, vault_flag=None, start_dir=vd
    )
    assert prof.name == "personal"
    assert prefix == "my-notes/"
    assert "my-notes" in banner


def test_resolve_in_vault_profile_override_resets_to_all(tmp_path):
    vd = _vault_dir(tmp_path)
    prof, prefix, _ = _resolve_scope(
        _cfg(), profile_flag="work", vault_flag=None, start_dir=vd
    )
    assert prof.name == "work"
    assert prefix is None  # override drops the .ki vault scope


def test_resolve_in_vault_vault_override(tmp_path):
    vd = _vault_dir(tmp_path)
    prof, prefix, _ = _resolve_scope(
        _cfg(), profile_flag=None, vault_flag="other", start_dir=vd
    )
    assert prof.name == "personal"  # still the bound profile
    assert prefix == "other/"


def test_resolve_bound_profile_missing_from_config_errors(tmp_path):
    vd = _vault_dir(tmp_path, profile="ghost")  # not in cfg
    with pytest.raises(ClickException) as e:
        _resolve_scope(_cfg(), profile_flag=None, vault_flag=None, start_dir=vd)
    assert "ghost" in str(e.value)


# ---- run_search param plumbing ---------------------------------------------


class _FakeSession:
    def __init__(self):
        self.params = None

    def run(self, _query, parameters=None):
        self.params = parameters
        return []


def test_run_search_threads_prefix_labels_k():
    s = _FakeSession()
    run_search(s, "q", vault_prefix="my-notes/", labels=["Section"], k=7)
    assert s.params["prefix"] == "my-notes/"
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
