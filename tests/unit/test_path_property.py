"""Unit tests for the `path` property on Folder / Document / Section rows.

`Vault.path` already existed pre-#40; this test module covers the new
machine-scoped `path` property on Folder, Document, and Section rows produced
by `src/ki/ingest/pipeline.py`.

Folder.path coverage lives in `test_folder_layer.py` (alongside the rest of
`_build_folder_and_tree_rows`'s tests). This module covers Document.path and
Section.path.
"""

from __future__ import annotations

from ki.ingest.pipeline import _document_row, _section_row
from ki.parser.markdown import assign_uris_and_content, parse_markdown

VAULT = "v-1"


def _parse_doc(text: str, *, filename: str = "doc.md", vault_uri: str = VAULT):
    doc = parse_markdown(text, filename=filename)
    doc_uri = f"{vault_uri}/{filename}"
    assign_uris_and_content(
        doc,
        document_uri=doc_uri,
        section_uri_fn=lambda hp: f"{doc_uri}#{'/'.join(hp)}",
    )
    return doc, doc_uri


def test_document_row_includes_path():
    doc, doc_uri = _parse_doc("# Title\n\nbody.\n")
    row = _document_row(doc, doc_uri, "/Users/zach/notes/doc.md")
    assert row["props"]["path"] == "/Users/zach/notes/doc.md"


def test_document_row_path_is_in_props_not_create_only():
    """`path` updates on re-ingest (machine-scoped) so it must live in `props`,
    not in `createOnly` — otherwise a vault re-indexed from a different mount
    would keep its old path forever."""
    doc, doc_uri = _parse_doc("# Title\n\nbody.\n")
    row = _document_row(doc, doc_uri, "/Users/zach/notes/doc.md")
    assert "path" in row["props"]
    assert "path" not in row.get("createOnly", {})


def test_section_row_includes_path_inherited_from_document():
    doc, doc_uri = _parse_doc("# Title\n\n## Sub\n\nbody.\n")
    file_path = "/Users/zach/notes/doc.md"
    rows = [_section_row(s, file_path) for s in doc.flat_sections]

    # Both top-level section and nested section carry the same path.
    assert len(rows) >= 2
    for row in rows:
        assert row["props"]["path"] == file_path


def test_section_path_is_owning_document_not_any_subsection_specific_thing():
    """All sections in a doc share one `path` value — the owning file path.
    No NEXT_SECTION position or heading slug should leak into this field."""
    doc, doc_uri = _parse_doc(
        "# A\n\n## B\n\ncontent.\n\n## C\n\nmore.\n## D\n\nlast.\n"
    )
    file_path = "/Users/zach/notes/multi.md"
    rows = [_section_row(s, file_path) for s in doc.flat_sections]
    distinct_paths = {row["props"]["path"] for row in rows}
    assert distinct_paths == {file_path}
