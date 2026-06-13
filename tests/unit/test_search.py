"""Unit tests for `ki search` — pure-Python pieces of `ki.commands.search`.

Covers --types CSV parsing, vault-scope prefix selection, run_search param
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
    _scope_prefix,
)
from ki.search.queries import run_search

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
    # `vault` is no longer a search type.
    with pytest.raises(ClickException):
        _parse_types("vault")


# ---- _scope_prefix ----------------------------------------------------------


def test_scope_prefix_all_vaults_is_none():
    assert _scope_prefix(all_vaults=True, vault_uri="my-notes", start_dir=None) is None


def test_scope_prefix_explicit_vault_gets_trailing_slash():
    assert (
        _scope_prefix(all_vaults=False, vault_uri="my-notes", start_dir=None)
        == "my-notes/"
    )
    # Idempotent if the caller already added a slash.
    assert (
        _scope_prefix(all_vaults=False, vault_uri="my-notes/", start_dir=None)
        == "my-notes/"
    )


def test_scope_prefix_outside_a_vault_is_none(tmp_path):
    # A dir with no .ki marker above it → no scope (search the whole profile).
    assert _scope_prefix(all_vaults=False, vault_uri=None, start_dir=tmp_path) is None


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


def test_search_help_has_vault_and_all_flags():
    res = CliRunner().invoke(main, ["search", "--help"])
    assert "--vault" in res.output
    assert "--all" in res.output


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
