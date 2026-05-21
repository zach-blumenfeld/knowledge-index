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


def test_piped_wikilink_captures_display_text():
    """`[[Target|Display]]` should expose `Display` as display_text."""
    text = "# Top\n\nsee [[Darth Vader|Anakin]] here.\n"
    doc = parse_markdown(text, filename="d.md")
    top = doc.sections[0]
    wl = [link for link in top.links if link.wikilink and not link.embed]
    assert len(wl) == 1
    assert wl[0].target == "Darth Vader"
    assert wl[0].display_text == "Anakin"


def test_unpiped_wikilink_has_no_display_text():
    """`[[Darth Vader]]` (no pipe) → display_text is None."""
    text = "# Top\n\nsee [[Darth Vader]] here.\n"
    doc = parse_markdown(text, filename="d.md")
    top = doc.sections[0]
    wl = [link for link in top.links if link.wikilink and not link.embed]
    assert len(wl) == 1
    assert wl[0].target == "Darth Vader"
    assert wl[0].display_text is None


def test_section_target_wikilink_carries_display_text():
    """`[[Doc#Section|Display]]` keeps `Doc#Section` as target and `Display` as text.

    The pipeline routes the display text to whichever endpoint the resolver
    picks (section URI here), but the parser's job is just to surface both
    fields verbatim. Resolution / routing is tested in the ingest tests.
    """
    text = "# Top\n\nsee [[Darth Vader#Origins|Anakin]] here.\n"
    doc = parse_markdown(text, filename="d.md")
    top = doc.sections[0]
    wl = [link for link in top.links if link.wikilink and not link.embed]
    assert len(wl) == 1
    assert wl[0].target == "Darth Vader#Origins"
    assert wl[0].display_text == "Anakin"


# ---- #37: external URLs + internal non-md file links ---------------------


def test_external_url_link_captured_as_external():
    text = "# Top\n\nsee [Launch blog](https://neo4j.com/blog/agentic-ai/).\n"
    doc = parse_markdown(text, filename="d.md")
    [link] = [link for link in doc.sections[0].links if link.kind == "external_url"]
    assert link.target == "https://neo4j.com/blog/agentic-ai/"
    assert link.display_text == "Launch blog"
    assert link.wikilink is False
    assert link.embed is False


def test_mailto_and_obsidian_schemes_are_external():
    """Any URI scheme — not just https — flags external_url per #37 q4."""
    text = (
        "# Top\n\n"
        "email [me](mailto:me@example.com), open [in obsidian]"
        "(obsidian://open?vault=blogs&file=foo).\n"
    )
    doc = parse_markdown(text, filename="d.md")
    kinds = {link.kind for link in doc.sections[0].links}
    assert "external_url" in kinds
    externals = {link.target for link in doc.sections[0].links if link.kind == "external_url"}
    assert "mailto:me@example.com" in externals
    assert "obsidian://open?vault=blogs&file=foo" in externals


def test_internal_non_md_link_captured_as_stub_kind():
    text = "# Top\n\nsee [Slides](./presentations/q3-deck.pptx) for the data.\n"
    doc = parse_markdown(text, filename="d.md")
    [link] = [
        link for link in doc.sections[0].links if link.kind == "non_md_file"
    ]
    assert link.target == "./presentations/q3-deck.pptx"
    assert link.display_text == "Slides"


def test_md_link_kind_is_md_link():
    """`[text](./foo.md)` is still classified as md_link (resolver path)."""
    text = "# Top\n\nsee [Foo](./foo.md).\n"
    doc = parse_markdown(text, filename="d.md")
    [link] = [link for link in doc.sections[0].links if not link.wikilink]
    assert link.kind == "md_link"
    assert link.target == "./foo.md"


def test_url_decode_only_applies_to_file_paths():
    """File paths get URL-decoded so they resolve on disk; URLs stay verbatim.

    Per #37 q1 (no URL normalization in v1), we don't decode URLs because
    that would silently fold `?foo=a%20b` and `?foo=a b` into the same node.
    """
    text = (
        "# Top\n\n"
        "[Deck](./my%20deck.pptx) and "
        "[URL](https://foo.com/a%20b).\n"
    )
    doc = parse_markdown(text, filename="d.md")
    links = doc.sections[0].links
    file_link = next(link for link in links if link.kind == "non_md_file")
    url_link = next(link for link in links if link.kind == "external_url")
    assert file_link.target == "./my deck.pptx"  # decoded
    assert url_link.target == "https://foo.com/a%20b"  # verbatim


def test_pure_fragment_link_skipped():
    """`[click](#anchor)` is a same-doc anchor — skipped (lives in section content)."""
    text = "# Top\n\nsee [the next section](#background) below.\n"
    doc = parse_markdown(text, filename="d.md")
    # Only the wikilink set (empty here) — no #-only links surface.
    md_links = [link for link in doc.sections[0].links if not link.wikilink]
    assert md_links == []


def test_image_embeds_not_treated_as_markdown_links():
    """`![alt](image.png)` is an image, not a link — must not be captured."""
    text = "# Top\n\n![Diagram](./arch.png)\n\nand [Slides](./deck.pptx).\n"
    doc = parse_markdown(text, filename="d.md")
    md_kind_links = [link for link in doc.sections[0].links if not link.wikilink]
    # Only `./deck.pptx`, not `./arch.png`.
    targets = {link.target for link in md_kind_links}
    assert "./deck.pptx" in targets
    assert "./arch.png" not in targets


def test_link_with_fragment_keeps_fragment_in_target_for_md():
    """`[click](./foo.md#bar)` keeps the fragment so the resolver can split it."""
    text = "# Top\n\nsee [Bar](./foo.md#bar) here.\n"
    doc = parse_markdown(text, filename="d.md")
    [link] = [link for link in doc.sections[0].links if link.kind == "md_link"]
    assert link.target == "./foo.md#bar"


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


# --- Frontmatter robustness (#53) ------------------------------------------


def test_frontmatter_with_control_char_recovers():
    """A stray ASCII control char (0x7F here) in a string value must not
    abort the parse — PyYAML refuses to read it, so we sanitize and retry."""
    text = (
        '---\n'
        'title: "Bones of the\x7f Milky Way"\n'
        'aliases: ["Nessie"]\n'
        '---\n'
        '# Body\n'
    )
    fm = parse_frontmatter(text, filename="dirty.md")
    assert fm.aliases == ["Nessie"]
    # Sanitization strips the control char, so the surrounding text remains.
    assert fm.frontmatter_json is not None
    assert "Bones of the Milky Way" in fm.frontmatter_json
    assert fm.body.startswith("# Body")


def test_frontmatter_completely_broken_falls_back_cleanly(caplog):
    """Syntactically broken YAML (unterminated quote) survives sanitization
    too — fall through to empty fields, body excludes the `---...---` block,
    and a warning naming the filename is logged."""
    text = (
        '---\n'
        'title: "missing closing quote\n'
        '---\n'
        'real body line\n'
    )
    with caplog.at_level("WARNING"):
        fm = parse_frontmatter(text, filename="broken.md")
    assert fm.aliases == []
    assert fm.frontmatter_created_at is None
    assert fm.frontmatter_json is None
    # The broken YAML block must not bleed into body content.
    assert "missing closing quote" not in fm.body
    assert "real body line" in fm.body
    # Warning logged with the filename.
    assert any("broken.md" in rec.message for rec in caplog.records)


def test_frontmatter_clean_yaml_unchanged():
    """Happy path: no sanitization, no warnings — round-trips as before."""
    text = (
        '---\n'
        'title: "Clean"\n'
        'aliases: ["A", "B"]\n'
        '---\n'
        'body\n'
    )
    fm = parse_frontmatter(text, filename="clean.md")
    assert fm.aliases == ["A", "B"]
    assert fm.frontmatter_json is not None
    assert "Clean" in fm.frontmatter_json
    assert fm.body.startswith("body")
