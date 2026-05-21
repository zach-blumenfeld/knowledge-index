"""Unit tests for `ki get` — pure-Python pieces of `ki.commands.get`.

Integration tests against an ephemeral Neo4j live in
`tests/integration/test_get.py`. These cover the rendering, shell-shape,
label-dispatch, and CLI-parsing logic without contacting the database.
"""

from __future__ import annotations

import json
from io import StringIO

import click as _click
from click.testing import CliRunner

from ki.cli import main
from ki.commands.get import (
    VALID_TYPES,
    _bad_label_message,
    _format_sections,
    _render,
    _render_one,
    _shell_for_label,
)

# ---- _shell_for_label ------------------------------------------------------


def _doc_b13_row() -> dict:
    return {
        "label": "Document",
        "uri": "vault://v/notes/big.md",
        "name": "big.md",
        "displayName": "big.md",
        "path": "/tmp/v/notes/big.md",
        "aliases": ["Big Idea", "BI"],
        "frontmatter": '{"tags": ["fixture"]}',
        "sourceType": "LOCAL_FILE",
        "firstLoadedAt": "2025-05-21T00:00:00",
        "lastLoadedAt": "2026-05-20T00:00:00",
        "headingLevel": None,
        "content": "preamble.\n\nuri: vault://v/notes/big.md#intro\n",
    }


def _section_b13_row() -> dict:
    return {
        "label": "Section",
        "uri": "vault://v/notes/big.md#intro",
        "name": "big/intro",
        "displayName": "Intro",
        "path": "/tmp/v/notes/big.md",
        "aliases": [],
        "frontmatter": None,
        "sourceType": None,
        "firstLoadedAt": None,
        "lastLoadedAt": None,
        "headingLevel": 2,
        "content": "section body.\n",
    }


def test_shell_for_document_includes_doc_specific_fields():
    shell = _shell_for_label(_doc_b13_row())
    assert shell["label"] == "Document"
    assert shell["frontmatter"] == '{"tags": ["fixture"]}'
    assert shell["sourceType"] == "LOCAL_FILE"
    assert shell["firstLoadedAt"] == "2025-05-21T00:00:00"
    assert shell["lastLoadedAt"] == "2026-05-20T00:00:00"
    # Section-specific field stays out.
    assert "headingLevel" not in shell


def test_shell_for_section_includes_heading_level():
    shell = _shell_for_label(_section_b13_row())
    assert shell["label"] == "Section"
    assert shell["headingLevel"] == 2
    # Doc-only fields stay out of the Section shell.
    assert "frontmatter" not in shell
    assert "sourceType" not in shell


def test_shell_carries_content_field_unchanged_when_type_content():
    """The default `--type content` returns B.13's content as-is (Rule 1 shape)."""
    shell = _shell_for_label(_doc_b13_row())
    assert "uri: vault://v/notes/big.md#intro" in shell["content"]


# ---- _format_sections ------------------------------------------------------


def test_format_sections_with_preamble_prepends_then_blank_line_then_headings():
    rows = [
        {"heading": "Intro", "heading_level": 2, "content": "intro body."},
        {"heading": "Body", "heading_level": 2, "content": "body text."},
    ]
    out = _format_sections(rows, preamble="doc preamble text.")
    # Preamble first, blank line, then each section as `## H\n\nbody`.
    assert out.startswith("doc preamble text.\n\n## Intro")
    assert "## Body\n\nbody text." in out


def test_format_sections_without_preamble_starts_at_first_heading():
    rows = [{"heading": "Only", "heading_level": 3, "content": "lone."}]
    out = _format_sections(rows, preamble=None)
    assert out == "### Only\n\nlone."


def test_format_sections_empty_preamble_is_skipped():
    rows = [{"heading": "Only", "heading_level": 1, "content": "x."}]
    out = _format_sections(rows, preamble="   \n  ")  # whitespace only
    assert out.startswith("# Only")


def test_format_sections_heading_only_no_body():
    """A section with a heading but no body still emits the heading."""
    rows = [{"heading": "Empty", "heading_level": 2, "content": ""}]
    out = _format_sections(rows, preamble=None)
    assert out == "## Empty"


# ---- _bad_label_message ----------------------------------------------------


def test_bad_label_message_folder_points_at_ki_tree():
    msg = _bad_label_message("Folder", "vault://v/projects")
    assert "Folder" in msg
    assert "vault://v/projects" in msg
    assert "ki tree --at vault://v/projects" in msg


def test_bad_label_message_vault_points_at_vault_list_and_tree():
    msg = _bad_label_message("Vault", "vault://v")
    assert "Vault" in msg
    assert "vault://v" in msg
    assert "ki vault list" in msg
    assert "ki tree --at vault://v" in msg


# ---- _render_one (plain text per-result rendering) -------------------------


def test_render_one_document_with_content():
    out = _render_one(_shell_for_label(_doc_b13_row()), get_type="content")
    assert "vault://v/notes/big.md" in out
    assert "label: Document" in out
    assert "path: /tmp/v/notes/big.md" in out
    assert "preamble." in out
    assert "uri: vault://v/notes/big.md#intro" in out


def test_render_one_document_with_path_type_omits_content():
    shell = _shell_for_label(_doc_b13_row())
    shell["content"] = None
    out = _render_one(shell, get_type="path")
    assert "preamble." not in out
    assert "no content emitted" in out
    assert "/tmp/v/notes/big.md" in out


def test_render_one_section_includes_heading_level():
    out = _render_one(_shell_for_label(_section_b13_row()), get_type="content")
    assert "label: Section" in out
    assert "headingLevel: 2" in out


# ---- CLI parsing -----------------------------------------------------------


def test_help_lists_get_command():
    runner = CliRunner()
    res = runner.invoke(main, ["--help"])
    assert res.exit_code == 0
    assert "get" in res.output


def test_get_help_lists_flags():
    runner = CliRunner()
    res = runner.invoke(main, ["get", "--help"])
    assert res.exit_code == 0
    for flag in ("--type", "--profile", "--json"):
        assert flag in res.output
    for type_val in VALID_TYPES:
        assert type_val in res.output


def test_get_requires_at_least_one_uri():
    runner = CliRunner()
    res = runner.invoke(main, ["get"])
    assert res.exit_code != 0


def test_get_rejects_unknown_type():
    runner = CliRunner()
    res = runner.invoke(main, ["get", "--type", "bogus", "vault://x/y.md"])
    assert res.exit_code != 0
    assert "bogus" in res.output or "bogus" in (res.stderr or "")


def test_get_accepts_all_three_valid_types_in_help():
    """Regression guard: if VALID_TYPES drifts from the CLI choice list, help loses an option."""
    runner = CliRunner()
    res = runner.invoke(main, ["get", "--help"])
    for type_val in VALID_TYPES:
        assert type_val in res.output


# ---- JSON render shape (no Neo4j) ------------------------------------------


def test_json_render_payload_shape():
    """The --json payload wraps results + errors under top-level keys."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        exit_code = _render(
            [_shell_for_label(_doc_b13_row())],
            [("vault://v/missing.md", "no node found for uri: vault://v/missing.md")],
            get_type="content",
            as_json=True,
        )
    # Exit code is 1 when there are errors.
    assert exit_code == 1


def test_json_render_payload_parses_clean():
    """Round-trip the JSON output to make sure it's valid JSON."""
    buf = StringIO()
    orig_echo = _click.echo

    def fake_echo(msg=None, **_kw):
        buf.write(msg if msg is not None else "")

    _click.echo = fake_echo
    try:
        _render(
            [_shell_for_label(_doc_b13_row())],
            [],
            get_type="path",
            as_json=True,
        )
    finally:
        _click.echo = orig_echo

    payload = json.loads(buf.getvalue())
    assert payload["type"] == "path"
    assert len(payload["results"]) == 1
    assert payload["results"][0]["uri"] == "vault://v/notes/big.md"
    assert payload["errors"] == []
