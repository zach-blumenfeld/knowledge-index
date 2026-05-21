"""Unit tests for `ki search` — pure-Python pieces of `ki.commands.search`.

Covers --types CSV parsing, label tagging via `_unify`, and table-render
header. Integration tests against an ephemeral Neo4j live in
`tests/integration/test_search.py`.
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
    _unify,
)

# ---- _parse_types ----------------------------------------------------------


def test_parse_types_default_is_all_three():
    assert _parse_types(DEFAULT_TYPES) == list(VALID_TYPES)


def test_parse_types_single():
    assert _parse_types("section") == ["section"]


def test_parse_types_csv_preserves_canonical_order():
    """User-provided order is normalized to VALID_TYPES order for determinism."""
    assert _parse_types("vault,document") == ["document", "vault"]


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


# ---- _unify (per-label display field extraction) ---------------------------


def test_unify_document_row():
    row = {
        "label": "Document",
        "score": 4.2,
        "document_uri": "vault://v/foo.md",
        "title": "foo.md",
        "path": "/tmp/foo.md",
    }
    u = _unify(row)
    assert u["label"] == "Document"
    assert u["displayName"] == "foo.md"
    assert u["uri"] == "vault://v/foo.md"
    assert u["score"] == 4.2


def test_unify_section_row_uses_heading_as_display_name():
    row = {
        "label": "Section",
        "score": 3.1,
        "section_uri": "vault://v/foo.md#background",
        "heading": "Background",
        "heading_level": 2,
    }
    u = _unify(row)
    assert u["label"] == "Section"
    # Per design: displayName is the heading text, NO `##` prefix.
    assert u["displayName"] == "Background"
    assert u["uri"] == "vault://v/foo.md#background"


def test_unify_vault_row_prefers_display_name_over_name():
    row = {
        "label": "Vault",
        "score": 2.5,
        "vault_uri": "vault://abc",
        "display_name": "My Blog",
        "name": "blog",
    }
    u = _unify(row)
    assert u["displayName"] == "My Blog"
    assert u["uri"] == "vault://abc"


def test_unify_vault_row_falls_back_to_name_if_display_name_missing():
    row = {
        "label": "Vault",
        "score": 2.5,
        "vault_uri": "vault://abc",
        "name": "blog",
    }
    u = _unify(row)
    assert u["displayName"] == "blog"


def test_type_letter_mapping_covers_all_valid_labels():
    """Regression guard: each label has a single-character T column letter."""
    assert TYPE_LETTER["Vault"] == "V"
    assert TYPE_LETTER["Document"] == "D"
    assert TYPE_LETTER["Section"] == "S"


# ---- CLI surface -----------------------------------------------------------


def test_search_help_shows_types_flag():
    runner = CliRunner()
    res = runner.invoke(main, ["search", "--help"])
    assert res.exit_code == 0
    assert "--types" in res.output
    for t in VALID_TYPES:
        assert t in res.output


def test_search_help_no_longer_has_singular_type_flag():
    """`--type` (singular) was renamed to `--types` (plural) in this PR.

    Confirm the singular form isn't lingering in help — `ki get` still uses
    `--type` for its own flag, but `ki search` should not.
    """
    runner = CliRunner()
    res = runner.invoke(main, ["search", "--help"])
    # Plural is present; singular '--type ' (with trailing space) should not be.
    assert "--types" in res.output
    assert "--type " not in res.output


def test_search_rejects_bogus_types_value():
    runner = CliRunner()
    res = runner.invoke(main, ["search", "foo", "--types", "bogus"])
    assert res.exit_code != 0
    assert "bogus" in res.output


def test_search_help_documents_default_is_all_types():
    """The flag's default value should be visible in --help."""
    runner = CliRunner()
    res = runner.invoke(main, ["search", "--help"])
    assert res.exit_code == 0
    assert DEFAULT_TYPES in res.output
