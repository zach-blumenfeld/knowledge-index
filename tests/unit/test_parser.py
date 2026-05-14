"""Markdown parser: section tree, NEXT_SECTION DFS order, frontmatter, links."""

from ki.parser.frontmatter import parse_frontmatter
from ki.parser.markdown import (
    assign_uris_and_content,
    document_content_from,
    hash_bytes,
    parse_markdown,
)
from ki.vault import section_uri


def test_section_tree_one_level_deep():
    text = "# Top\n\nintro\n\n## Child A\n\nA body\n\n## Child B\n\nB body\n"
    doc = parse_markdown(text, filename="doc.md")
    assert len(doc.sections) == 1
    top = doc.sections[0]
    assert top.heading_text == "Top"
    assert len(top.children) == 2
    assert [c.heading_text for c in top.children] == ["Child A", "Child B"]


def test_next_section_dfs_crosses_levels():
    """H1 → H2 → H3 → H1 — DFS order through every section."""
    text = (
        "# H1A\n"
        "## H2A\n"
        "### H3A\n"
        "## H2B\n"
        "# H1B\n"
    )
    doc = parse_markdown(text, filename="d.md")
    titles = [s.heading_text for s in doc.flat_sections]
    assert titles == ["H1A", "H2A", "H3A", "H2B", "H1B"]


def test_skipped_heading_levels_keep_real_level(tmp_path):
    """Rule 2: H1 → H3 (skipping H2). H3 is direct child of H1, headingLevel=3."""
    text = "# Top\n\n### Deep\n\nstuff\n"
    doc = parse_markdown(text, filename="d.md")
    top = doc.sections[0]
    assert len(top.children) == 1
    deep = top.children[0]
    assert deep.heading_level == 3
    assert deep.heading_text == "Deep"


def test_duplicate_heading_disambiguation_per_parent():
    """Rule 3: duplicates at same level under same parent get -1, -2, ..."""
    text = (
        "## Installation\n"
        "first body\n"
        "## Installation\n"
        "second body\n"
        "## Installation\n"
        "third body\n"
    )
    doc = parse_markdown(text, filename="d.md")
    assert len(doc.flat_sections) == 3
    slugs = [s.heading_path[-1] for s in doc.flat_sections]
    assert slugs == ["installation", "installation-1", "installation-2"]


def test_duplicate_headings_scoped_per_parent():
    """Same-named headings under different parents do NOT conflict."""
    text = (
        "# Foo\n"
        "## Overview\n"
        "# Bar\n"
        "## Overview\n"
    )
    doc = parse_markdown(text, filename="d.md")
    # Both Overviews should slug to plain "overview" because they have
    # different parents (Foo and Bar respectively).
    overviews = [s for s in doc.flat_sections if s.heading_text == "Overview"]
    assert len(overviews) == 2
    assert overviews[0].heading_path == ["foo", "overview"]
    assert overviews[1].heading_path == ["bar", "overview"]


def test_frontmatter_extraction():
    text = (
        "---\n"
        "aliases:\n"
        "  - JFK\n"
        "  - John F Kennedy\n"
        "created: 2024-05-14\n"
        "tags: [history, presidents]\n"
        "custom_key: value\n"
        "---\n"
        "\n"
        "# Body Heading\n"
    )
    fm = parse_frontmatter(text)
    assert fm.aliases == ["JFK", "John F Kennedy"]
    assert fm.frontmatter_created_at is not None
    assert fm.frontmatter_created_at.year == 2024
    # The unknown blob should serialize the remaining keys (tags, custom_key)
    assert fm.frontmatter_json is not None
    assert "tags" in fm.frontmatter_json
    assert "custom_key" in fm.frontmatter_json
    # aliases / created should have been removed from the blob
    assert "aliases" not in fm.frontmatter_json
    assert "created" not in fm.frontmatter_json
    # Body should start with the heading, not the frontmatter
    assert fm.body.lstrip().startswith("# Body Heading")


def test_file_hash_is_stable():
    h1 = hash_bytes(b"hello")
    h2 = hash_bytes(b"hello")
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex
    assert h1 != hash_bytes(b"hello!")


def test_wikilink_and_markdown_link_extraction():
    text = (
        "# Top\n"
        "see [[Note One]] and ![[Image]] and [text](./other.md) here.\n"
    )
    doc = parse_markdown(text, filename="d.md")
    top = doc.sections[0]
    targets = {(link.target, link.wikilink, link.embed) for link in top.links}
    assert ("Note One", True, False) in targets
    assert ("Image", True, True) in targets
    assert ("./other.md", False, False) in targets


def test_content_construction_shallow_with_pointers():
    """Rule 1: Section.content = body + uri: lines for direct children."""
    text = (
        "# Parent\n"
        "parent body\n"
        "## Child\n"
        "child body\n"
    )
    doc = parse_markdown(text, filename="d.md")
    doc_uri = "vault-1/d.md"
    assign_uris_and_content(
        doc,
        document_uri=doc_uri,
        section_uri_fn=lambda hp: section_uri(doc_uri, hp),
    )
    parent = doc.sections[0]
    child = parent.children[0]
    # Child content has the child body (no further pointers since no grandchild)
    assert "child body" in child.content
    # Parent content has parent body AND a `uri:` pointer to the child.
    assert "parent body" in parent.content
    assert f"uri:{child.uri}" in parent.content
    # Child body must NOT be embedded in parent content.
    assert "child body" not in parent.content

    # Document content = preamble (none here) + uri pointers to top-level sections.
    doc_content = document_content_from(doc)
    assert f"uri:{parent.uri}" in doc_content


def test_preamble_captured_before_first_heading():
    text = "intro line one\nintro line two\n\n# First Heading\nbody\n"
    doc = parse_markdown(text, filename="d.md")
    assert "intro line one" in doc.preamble
    assert "intro line two" in doc.preamble


def test_document_with_no_headings_has_preamble_only():
    text = "just some text, no headings at all\n"
    doc = parse_markdown(text, filename="d.md")
    assert doc.flat_sections == []
    assert "just some text" in doc.preamble
