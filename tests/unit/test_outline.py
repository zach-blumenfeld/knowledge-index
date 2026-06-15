"""Unit tests for the `ki outline` renderer.

These cover the pure-Python functions in `ki.commands.outline`: parsing
the positional URI / `--at`, grouping rows by parent_uri, applying the
per-group sort rules, DFS-emitting, and formatting the final text
output. No Neo4j involved.

Integration tests (B.12 / B.12-links against an ephemeral Neo4j) live in
`tests/integration/test_search.py`.
"""

from __future__ import annotations

import pytest
from click import ClickException

from ki.commands.outline import (
    Row,
    _dfs_emit,
    _format_rows,
    _group_and_sort,
    _left_string,
    _links_to_hint,
    _parse_at,
    _uri_display,
    cmd_outline,
)

# ---- render-root resolution (no Neo4j; errors before connecting) ----------


def _write_cfg(tmp_path, monkeypatch):
    xdg = tmp_path / "xdg"
    (xdg / "ki").mkdir(parents=True)
    (xdg / "ki" / "config.yaml").write_text(
        "profiles:\n  local:\n    uri: bolt://h:7687\n    user: neo4j\n    password: x\n"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("KI_PROFILE", raising=False)


def test_outline_bare_outside_vault_errors(tmp_path, monkeypatch):
    """No uri, no --profile, not in a vault → a clear error (nothing to render),
    raised before any Neo4j connection."""
    _write_cfg(tmp_path, monkeypatch)
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(ClickException) as e:
        cmd_outline(profile=None, at=None, depth=2, full=False, directory=plain)
    assert "needs a target" in str(e.value)


# ---- --at parsing ----------------------------------------------------------


def test_parse_at_none_returns_none():
    assert _parse_at(None) is None


def test_parse_at_label_uri_form_strips_label():
    assert _parse_at("Vault:vault://abc-123") == "vault://abc-123"


def test_parse_at_bare_uri_returned_unchanged():
    assert _parse_at("vault://abc-123/ideas/foo.md") == "vault://abc-123/ideas/foo.md"


def test_parse_at_section_uri_with_label():
    assert (
        _parse_at("Section:vault://abc-123/foo.md#bar")
        == "vault://abc-123/foo.md#bar"
    )


def test_parse_at_external_url_returned_verbatim():
    """External URL Documents (#37) use the URL itself as the URI. The
    leading `https:` is NOT a `Label:` prefix and must not be stripped —
    historical regression where `_parse_at` partitioned on the first
    colon and turned `https://beltagy.net/` into `//beltagy.net/`.
    """
    assert _parse_at("https://beltagy.net/") == "https://beltagy.net/"
    assert (
        _parse_at("http://example.com/foo?x=1")
        == "http://example.com/foo?x=1"
    )
    assert (
        _parse_at("file:///Users/zach/notes/foo.md")
        == "file:///Users/zach/notes/foo.md"
    )


def test_parse_at_document_label_with_external_url():
    """`Document:https://beltagy.net/` is a legitimate Label:uri form for
    an external URL Document — strip only the `Document:` prefix, keep
    the full URL intact."""
    assert (
        _parse_at("Document:https://beltagy.net/")
        == "https://beltagy.net/"
    )


def test_parse_at_unknown_prefix_treated_as_bare_uri():
    """A `scheme:` prefix that isn't one of the four node labels is part
    of the URI, not a Label prefix. Fail-noisy on a real lookup beats
    silently chopping the scheme."""
    # `obsidian://` could become a real URI in the future (#22).
    assert _parse_at("obsidian://open?vault=foo") == "obsidian://open?vault=foo"


# ---- group + sort ----------------------------------------------------------


def test_group_and_sort_folders_alphabetical():
    rows = [
        Row(0, None, "Vault", "v", "v", "vault://v", None, None),
        Row(1, "HAS", "Folder", "zebra", "zebra", "vault://v/zebra", "vault://v", None),
        Row(1, "HAS", "Folder", "alpha", "alpha", "vault://v/alpha", "vault://v", None),
    ]
    by_parent = _group_and_sort(rows)
    names = [r.name for r in by_parent["vault://v"]]
    assert names == ["alpha", "zebra"]


def test_group_and_sort_sections_use_sort_pos_not_alpha():
    """Sections must order by NEXT_SECTION position, not by name."""
    rows = [
        Row(2, "HAS", "Document", "doc.md", "doc", "vault://v/doc.md", "vault://v", None),
        # `appendix` would come first alphabetically, but its sort_pos is 2.
        Row(3, "HAS", "Section", "appendix", "Appendix", "vault://v/doc.md#appendix", "vault://v/doc.md", 2),
        Row(3, "HAS", "Section", "intro", "Intro", "vault://v/doc.md#intro", "vault://v/doc.md", 0),
        Row(3, "HAS", "Section", "body", "Body", "vault://v/doc.md#body", "vault://v/doc.md", 1),
    ]
    by_parent = _group_and_sort(rows)
    names = [r.name for r in by_parent["vault://v/doc.md"]]
    assert names == ["intro", "body", "appendix"]


def test_group_and_sort_links_sort_by_target_uri_after_sections():
    rows = [
        Row(2, "HAS", "Section", "sec-a", "Sec A", "vault://v/doc.md#sec-a", "vault://v/doc.md", 0),
        Row(3, "LINKS_TO", "Section", "z", "Z", "vault://other/z#z", "vault://v/doc.md#sec-a", None),
        Row(3, "LINKS_TO", "Section", "a", "A", "vault://other/a#a", "vault://v/doc.md#sec-a", None),
    ]
    by_parent = _group_and_sort(rows)
    kids = by_parent["vault://v/doc.md#sec-a"]
    # Links sort alphabetically by target uri.
    assert [k.uri for k in kids] == ["vault://other/a#a", "vault://other/z#z"]


def test_group_and_sort_multi_vault_roots_alphabetical():
    rows = [
        Row(0, None, "Vault", "second", "second", "vault://2", None, None),
        Row(0, None, "Vault", "alpha", "alpha", "vault://1", None, None),
    ]
    by_parent = _group_and_sort(rows)
    assert [r.name for r in by_parent[None]] == ["alpha", "second"]


def test_group_and_sort_mixed_section_then_links():
    """Under a Document, HAS-Section group renders before LINKS_TO group."""
    rows = [
        Row(1, "HAS", "Section", "intro", "Intro", "vault://v/doc.md#intro", "vault://v/doc.md", 0),
        Row(1, "LINKS_TO", "Section", "x", "X", "vault://other/x#x", "vault://v/doc.md", None),
    ]
    by_parent = _group_and_sort(rows)
    kids = by_parent["vault://v/doc.md"]
    assert [k.inrel for k in kids] == ["HAS", "LINKS_TO"]


# ---- DFS emit --------------------------------------------------------------


def test_dfs_emit_walks_root_then_children_recursively():
    rows = [
        Row(0, None, "Vault", "v", "v", "vault://v", None, None),
        Row(1, "HAS", "Folder", "f", "f", "vault://v/f", "vault://v", None),
        Row(2, "HAS", "Document", "d.md", "d", "vault://v/f/d.md", "vault://v/f", None),
    ]
    by_parent = _group_and_sort(rows)
    emitted = _dfs_emit(by_parent)
    assert [r.uri for r in emitted] == [
        "vault://v",
        "vault://v/f",
        "vault://v/f/d.md",
    ]


def test_dfs_emit_multi_root_emits_each_in_order():
    rows = [
        Row(0, None, "Vault", "alpha", "alpha", "vault://1", None, None),
        Row(0, None, "Vault", "beta", "beta", "vault://2", None, None),
        Row(1, "HAS", "Folder", "x", "x", "vault://1/x", "vault://1", None),
        Row(1, "HAS", "Folder", "y", "y", "vault://2/y", "vault://2", None),
    ]
    by_parent = _group_and_sort(rows)
    emitted = _dfs_emit(by_parent)
    uris = [r.uri for r in emitted]
    # alpha tree, then beta tree (alphabetical at root group).
    assert uris == ["vault://1", "vault://1/x", "vault://2", "vault://2/y"]


def test_dfs_emit_terminates_on_links_to_cycle():
    """Regression: `ki outline --at <folder> --depth 3` previously hit a
    RecursionError when a section linked back to an ancestor in the same
    subtree (issue #60). The renderer guards with a `visited` set.

    Cycle setup: Folder → Doc → Section -[LINKS_TO]-> Doc (back-edge).
    Without the guard, emit(doc) → emit(section) → emit(doc) → … indefinitely.
    """
    folder_uri = "vault://v/themes"
    doc_uri = "vault://v/themes/big-idea.md"
    section_uri = "vault://v/themes/big-idea.md#origins"
    rows = [
        Row(0, None, "Folder", "themes", "themes/", folder_uri, None, None),
        Row(1, "HAS", "Document", "big-idea.md", "big-idea.md", doc_uri, folder_uri, None),
        Row(2, "HAS", "Section", "origins", "Origins", section_uri, doc_uri, 0),
        # LINKS_TO back-edge: the section links to its parent document.
        # Without the cycle guard, DFS would recurse from doc → section →
        # doc → section → … forever.
        Row(3, "LINKS_TO", "Document", "big-idea.md", "big-idea.md", doc_uri, section_uri, None),
    ]
    by_parent = _group_and_sort(rows)
    emitted = _dfs_emit(by_parent)
    emitted_uris = [r.uri for r in emitted]
    # Each HAS-tree node appears exactly once.
    assert emitted_uris.count(folder_uri) == 1
    assert emitted_uris.count(section_uri) == 1
    # The doc appears twice: once as a HAS-child of the folder, once as a
    # LINKS_TO target under the section. That's the correct rendering — the
    # link is visible, but its subtree isn't re-expanded.
    assert emitted_uris.count(doc_uri) == 2


def test_dfs_emit_terminates_on_section_self_cycle():
    """Even tighter cycle: a section that links to itself. The guard must
    still emit the LINKS_TO row once and stop recursing."""
    section_uri = "vault://v/doc.md#sec"
    doc_uri = "vault://v/doc.md"
    rows = [
        Row(0, None, "Document", "doc.md", "doc.md", doc_uri, None, None),
        Row(1, "HAS", "Section", "sec", "Sec", section_uri, doc_uri, 0),
        Row(2, "LINKS_TO", "Section", "sec", "Sec", section_uri, section_uri, None),
    ]
    by_parent = _group_and_sort(rows)
    emitted = _dfs_emit(by_parent)
    # Section appears twice: once as HAS child, once as LINKS_TO target.
    assert [r.uri for r in emitted].count(section_uri) == 2


# ---- left-string composition ----------------------------------------------


def test_left_string_folder_has_trailing_slash():
    r = Row(1, "HAS", "Folder", "ideas", "ideas", "vault://v/ideas", "vault://v", None)
    assert _left_string(r) == "  ideas/"


def test_left_string_document_renders_display_name():
    """Document rows render `displayName` (matches Section's rule).

    After #37 stubs / external Documents carry the link-text label in
    `displayName` (e.g. `"Q3 deck"` for a non-md stub, `"Launch blog"`
    for a URL). The historical `name  "displayName"` rendering — written
    for pre-#28 H1-promoted display names — is intentionally removed; the
    URI column carries the filename / URL alongside.
    """
    r = Row(2, "HAS", "Document", "foo.md", "Foo Document", "vault://v/foo.md", "vault://v", None)
    assert _left_string(r) == "    Foo Document"


def test_left_string_document_with_matching_display_name():
    """When `name == displayName` (the common md-doc case after #28), the
    rendered name is just that string."""
    r = Row(2, "HAS", "Document", "foo.md", "foo.md", "vault://v/foo.md", "vault://v", None)
    assert _left_string(r) == "    foo.md"


def test_left_string_section_uses_display_name():
    r = Row(3, "HAS", "Section", "big-idea", "Big Idea", "vault://v/d.md#big-idea", "vault://v/d.md", 0)
    assert _left_string(r) == "      Big Idea"


def test_left_string_links_to_uses_arrow_prefix():
    r = Row(4, "LINKS_TO", "Section", "refs/birth.md/tgt", "Target", "my-notes/refs/birth.md#tgt", "my-notes/d.md#x", None)
    assert _left_string(r) == "        → Target"


# ---- URI display rules ----------------------------------------------------


def test_uri_display_has_section_shows_full_uri():
    """Every row shows the full URI so it's copy-pasteable into the next
    `ki tree --at <uri>` or `ki get <uri>` — no shorthand for sections."""
    r = Row(3, "HAS", "Section", "x", "X", "vault://v/d.md#x", "vault://v/d.md", 0)
    assert _uri_display(r) == "vault://v/d.md#x"


def test_uri_display_links_to_section_shows_full_uri():
    r = Row(3, "LINKS_TO", "Section", "x", "X", "vault://other/d.md#x", "vault://v/d.md", None)
    assert _uri_display(r) == "vault://other/d.md#x"


def test_uri_display_document_shows_full_uri():
    r = Row(2, "HAS", "Document", "d.md", "d.md", "vault://v/d.md", "vault://v", None)
    assert _uri_display(r) == "vault://v/d.md"


# ---- LINKS_TO hint --------------------------------------------------------


def test_links_to_hint_uses_display_name():
    """The hint is the target's displayName — heading for Section, filename
    for Document, link-text for #37 external / stub targets. The URI column
    next to it carries the full URI, so the hint never needs to repeat it.
    """
    # Section target — displayName is the heading text.
    r_section = Row(
        3, "LINKS_TO", "Section", "refs/birth.md/x", "Early Draft",
        "my-notes/refs/birth.md#x", "my-notes/d.md", None,
    )
    assert _links_to_hint(r_section) == "Early Draft"

    # External URL target — displayName is the link text from `[text](url)`.
    r_url = Row(
        3, "LINKS_TO", "Document",
        "https://neo4j.com/blog/agentic-ai/",
        "Launch blog",
        "https://neo4j.com/blog/agentic-ai/", "my-notes/d.md", None,
    )
    assert _links_to_hint(r_url) == "Launch blog"

    # Internal Document target — displayName is the filename.
    r_doc = Row(
        3, "LINKS_TO", "Document", "big-idea.md", "big-idea.md",
        "my-notes/ideas/big-idea.md", "my-notes/d.md", None,
    )
    assert _links_to_hint(r_doc) == "big-idea.md"


def test_links_to_hint_falls_back_to_links_to_when_displayname_missing():
    """Defensive fallback — surfaces a visible breadcrumb instead of silently
    repeating the URI (which is already in the URI column on the same row)."""
    r = Row(3, "LINKS_TO", "Document", "http://example.com/foo", "", "http://example.com/foo", "my-notes/d.md", None)
    assert _links_to_hint(r) == "links_to"


# ---- format / render ------------------------------------------------------


def test_format_rows_empty_returns_no_results():
    assert "no results" in _format_rows([], full=False)


def test_format_rows_emits_key_line_and_header():
    rows = [Row(0, None, "Vault", "v", "v", "vault://v", None, None)]
    out = _format_rows(rows, full=False).splitlines()
    assert out[0].startswith("Key:")
    assert "V Vault" in out[0]
    assert "L Links-to" in out[0]
    # Blank line between key and column header.
    assert out[1] == ""
    assert "NAME" in out[2] and out[2].rstrip().endswith("URI")


def test_format_rows_type_letter_for_links_to_is_L_not_label_initial():
    """A LINKS_TO row whose target is a Section must render 'L' in the type column."""
    rows = [
        Row(0, None, "Vault", "v", "v", "vault://v", None, None),
        Row(1, "HAS", "Document", "d.md", "d.md", "vault://v/d.md", "vault://v", None),
        Row(2, "LINKS_TO", "Section", "tgt", "Target", "vault://v/other.md#tgt", "vault://v/d.md", None),
    ]
    out = _format_rows(rows, full=False)
    # The LINKS_TO row's type column should be 'L', not 'S'.
    link_line = next(line for line in out.splitlines() if "→" in line)
    assert " L   " in link_line
    assert " S   " not in link_line


def test_format_rows_truncates_long_names_with_ellipsis():
    long_name = "a-very-long-document-filename-that-overflows-the-name-column.md"
    rows = [
        Row(0, None, "Vault", "v", "v", "vault://v", None, None),
        Row(1, "HAS", "Document", long_name, long_name, f"vault://v/{long_name}", "vault://v", None),
    ]
    out = _format_rows(rows, full=False)
    long_line = next(line for line in out.splitlines() if long_name[:10] in line)
    assert "…" in long_line
    # URI column is always the full URI, never truncated.
    assert f"vault://v/{long_name}" in long_line


def test_format_rows_full_adds_vault_description_subline():
    rows = [
        Row(0, None, "Vault", "v", "v", "vault://v", None, None, description="A test vault."),
    ]
    out = _format_rows(rows, full=True)
    assert "> A test vault." in out


def test_format_rows_full_without_vault_description_emits_no_subline():
    rows = [
        Row(0, None, "Vault", "v", "v", "vault://v", None, None, description=None),
    ]
    out = _format_rows(rows, full=True)
    assert ">" not in out


def test_format_rows_full_joins_multiline_vault_description():
    rows = [
        Row(0, None, "Vault", "v", "v", "vault://v", None, None, description="line one\nline two"),
    ]
    out = _format_rows(rows, full=True)
    assert "> line one line two" in out


def test_format_rows_indentation_uses_two_spaces_per_depth():
    rows = [
        Row(0, None, "Vault", "v", "v", "vault://v", None, None),
        Row(1, "HAS", "Folder", "f", "f", "vault://v/f", "vault://v", None),
        Row(2, "HAS", "Document", "d.md", "d.md", "vault://v/f/d.md", "vault://v/f", None),
    ]
    out = _format_rows(rows, full=False).splitlines()
    # First content row is the vault, at depth 0 (no leading space).
    vault_row = next(line for line in out if "v " in line or "v ." in line)
    assert vault_row.startswith("v ")
    folder_row = next(line for line in out if line.lstrip().startswith("f/"))
    assert folder_row.startswith("  f/")
    doc_row = next(line for line in out if "d.md" in line)
    assert doc_row.startswith("    d.md")
