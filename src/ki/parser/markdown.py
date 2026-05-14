"""Markdown → Document + Section tree.

Implements the *Content Construction Rules* from docs/data-model.md:

  Rule 1 — Shallow content with child pointers.
      Each node's `content` field is the body text *directly* under it
      followed by `uri:` references to its direct children. Child body
      text is never included.

  Rule 2 — Skipped heading levels.
      If a document jumps from H1 to H3 (skipping H2), the H3 becomes
      a *direct* child of the H1 in the tree. `headingLevel` reflects
      the real level (3); no synthetic H2 is inserted.

  Rule 3 — Duplicate heading disambiguation.
      Duplicate headings *at the same nesting level under the same parent*
      get `-1`, `-2`, ... starting from the second occurrence
      (GitHub/Pandoc convention).

`NEXT_SECTION` reading order is the order in which sections appear in the
file (top-to-bottom = DFS reading order, naturally crossing heading levels).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from markdown_it import MarkdownIt

from ..vault import slugify_segment
from .frontmatter import FrontmatterFields, parse_frontmatter

# Wikilink and embed forms. Order matters: embed must be matched first.
# Capture both the target and an optional piped display text so the ingest
# pipeline can route `[[Target|Display]]` display texts back to the target's
# aliases list (see docs/ingest-cypher.md §4.3 step 7).
_EMBED_RE = re.compile(r"!\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")
_WIKILINK_RE = re.compile(r"(?<!\!)\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")
# Markdown link to an .md file (relative or absolute). Captures href.
_MD_LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+\.md(?:#[^)]*)?)\)")


@dataclass
class ParsedLink:
    target: str  # raw target text (wikilink name or markdown href)
    wikilink: bool
    embed: bool
    # Display text after the pipe in a wikilink (`[[Target|Display]]`).
    # `None` for unpiped wikilinks, embeds-without-pipe, and markdown links.
    # Routed to the *target's* `aliases` at ingest — see docs/ingest-cypher.md
    # §4.3 step 7.
    display_text: str | None = None


@dataclass
class ParsedSection:
    heading_text: str  # raw heading text as written
    heading_level: int
    heading_path: list[str]  # disambiguated slugs root → here
    body: str  # raw markdown text between this heading and the next heading
    children: list[ParsedSection] = field(default_factory=list)
    # Filled later in the pipeline:
    uri: str = ""
    content: str = ""  # body + `uri:` lines for direct children (Rule 1)
    links: list[ParsedLink] = field(default_factory=list)


@dataclass
class ParsedDocument:
    name: str  # filename basename incl. extension (e.g. "ideas.md")
    display_name: str
    aliases: list[str]
    file_hash: str
    frontmatter_json: str | None
    frontmatter_created_at: datetime | None
    preamble: str  # text before the first heading
    sections: list[ParsedSection]  # top-level (direct children of the Document)
    flat_sections: list[ParsedSection]  # all sections in DFS reading order
    document_links: list[ParsedLink]  # links found in preamble (attached to Document)


# --- core parse --------------------------------------------------------------


def _split_into_blocks(body: str) -> list[tuple[int | None, str, str]]:
    """Walk the markdown using markdown-it tokens.

    Returns a list of (heading_level, heading_text, body_text_following) tuples,
    in file order. The first tuple may have `heading_level=None` if the document
    has a preamble before any heading.
    """
    md = MarkdownIt("commonmark")
    tokens = md.parse(body)

    # Find all heading tokens and their source-line spans. markdown-it-py tokens
    # have a `map` attribute = [start_line, end_line) into the original source.
    heading_spans: list[tuple[int, int, str]] = []  # (level, start_line, heading_text)
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.type == "heading_open":
            level = int(tok.tag[1:])  # "h2" -> 2
            start_line = tok.map[0] if tok.map else 0
            # next token is the inline content
            inline = tokens[i + 1] if i + 1 < len(tokens) else None
            heading_text = (inline.content if inline else "").strip()
            heading_spans.append((level, start_line, heading_text))
            i += 3  # skip heading_open / inline / heading_close
            continue
        i += 1

    lines = body.splitlines()
    blocks: list[tuple[int | None, str, str]] = []
    if not heading_spans:
        # Whole doc is preamble
        return [(None, "", body)]

    # Preamble = everything before the first heading.
    first_start = heading_spans[0][1]
    if first_start > 0:
        blocks.append((None, "", "\n".join(lines[:first_start])))

    for idx, (level, start_line, heading_text) in enumerate(heading_spans):
        next_start = heading_spans[idx + 1][1] if idx + 1 < len(heading_spans) else len(lines)
        # Body of this section = everything after the heading line up to the next heading
        body_lines = lines[start_line + 1 : next_start]
        blocks.append((level, heading_text, "\n".join(body_lines)))

    return blocks


def _build_tree(
    blocks: list[tuple[int | None, str, str]],
) -> tuple[str, list[ParsedSection], list[ParsedSection]]:
    """Build the section tree from the flat (level, text, body) list.

    Returns (preamble, top_level_sections, dfs_ordered_sections).
    """
    preamble = ""
    top_level: list[ParsedSection] = []
    dfs: list[ParsedSection] = []
    stack: list[ParsedSection] = []  # path from root to current
    # Per-parent base-slug counters for duplicate disambiguation.
    # Keyed by id(parent_section) or 0 for document-level top-list.
    base_slug_counts: dict[int, dict[str, int]] = {0: {}}

    for level, heading_text, body in blocks:
        if level is None:
            preamble = body.strip("\n")
            continue
        # Pop until we find a section with strictly smaller level (the parent).
        while stack and stack[-1].heading_level >= level:
            stack.pop()
        parent_id = id(stack[-1]) if stack else 0
        if parent_id not in base_slug_counts:
            base_slug_counts[parent_id] = {}
        base = slugify_segment(heading_text)
        n_prior = base_slug_counts[parent_id].get(base, 0)
        slug = base if n_prior == 0 else f"{base}-{n_prior}"
        base_slug_counts[parent_id][base] = n_prior + 1
        parent_path = stack[-1].heading_path if stack else []
        section = ParsedSection(
            heading_text=heading_text,
            heading_level=level,
            heading_path=parent_path + [slug],
            body=body.strip("\n"),
        )
        if stack:
            stack[-1].children.append(section)
        else:
            top_level.append(section)
        dfs.append(section)
        stack.append(section)

    return preamble, top_level, dfs


def _extract_links(text: str) -> list[ParsedLink]:
    links: list[ParsedLink] = []
    for m in _EMBED_RE.finditer(text):
        display = m.group(2)
        links.append(
            ParsedLink(
                target=m.group(1).strip(),
                wikilink=True,
                embed=True,
                display_text=display.strip() if display else None,
            )
        )
    for m in _WIKILINK_RE.finditer(text):
        display = m.group(2)
        links.append(
            ParsedLink(
                target=m.group(1).strip(),
                wikilink=True,
                embed=False,
                display_text=display.strip() if display else None,
            )
        )
    for m in _MD_LINK_RE.finditer(text):
        links.append(ParsedLink(target=m.group(1).strip(), wikilink=False, embed=False))
    return links


def parse_markdown(text: str, *, filename: str) -> ParsedDocument:
    """Parse a markdown string into the in-memory document model.

    `filename` is the source basename (e.g. "ideas.md"). It's used for
    Document.name and Document.displayName per docs/data-model.md.
    """
    fm: FrontmatterFields = parse_frontmatter(text)

    blocks = _split_into_blocks(fm.body)
    preamble, top_level, dfs = _build_tree(blocks)

    # Link extraction: per-section + preamble.
    doc_links = _extract_links(preamble) if preamble else []
    for sec in dfs:
        sec.links = _extract_links(sec.body)

    file_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    return ParsedDocument(
        name=filename,
        display_name=filename,
        aliases=fm.aliases,
        file_hash=file_hash,
        frontmatter_json=fm.frontmatter_json,
        frontmatter_created_at=fm.frontmatter_created_at,
        preamble=preamble,
        sections=top_level,
        flat_sections=dfs,
        document_links=doc_links,
    )


def hash_bytes(b: bytes) -> str:
    """SHA-256 over raw bytes. Used to fileHash-skip unchanged files."""
    return hashlib.sha256(b).hexdigest()


# --- content construction (Rule 1) -----------------------------------------


def assign_uris_and_content(
    doc: ParsedDocument,
    *,
    document_uri: str,
    section_uri_fn: Any,  # callable: heading_path -> str
) -> None:
    """Assign `uri` and shallow `content` to every section + the document.

    `content` per Rule 1: body text under this node, then one `uri:` line per
    direct child. Document.content = preamble + child URI pointers.
    """
    # First pass: assign URIs.
    for sec in doc.flat_sections:
        sec.uri = section_uri_fn(sec.heading_path)

    # Second pass: build content. Section.content = body + uri: lines for
    # direct children (Rule 1). Document.content is composed separately by
    # `document_content_from` so we don't mutate doc.preamble.
    for sec in doc.flat_sections:
        lines: list[str] = []
        if sec.body.strip():
            lines.append(sec.body.strip())
        for child in sec.children:
            lines.append(f"uri:{child.uri}")
        sec.content = "\n\n".join(lines)


def document_content_from(doc: ParsedDocument) -> str:
    """Compose Document.content per Rule 1.

    Preamble text (text before the first heading) followed by `uri:` pointers
    to each direct (top-level) section.
    """
    lines: list[str] = []
    if doc.preamble.strip():
        lines.append(doc.preamble.strip())
    for sec in doc.sections:
        if sec.uri:
            lines.append(f"uri:{sec.uri}")
    return "\n\n".join(lines)
