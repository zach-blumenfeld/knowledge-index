"""Slugification matches the docs/data-model.md *Path conventions* examples."""

from ki.vault import document_uri, slugify_path, slugify_segment


def test_slugify_segment_lowercases_and_dashifies():
    assert slugify_segment("Big Idea") == "big-idea"


def test_slugify_segment_preserves_underscores_and_dots():
    assert slugify_segment("_index.md") == "_index.md"


def test_slugify_segment_strips_punctuation():
    assert slugify_segment("Hello, World!") == "hello-world"
    assert slugify_segment("Foo / Bar") == "foo-bar"  # segment-internal '/' collapses to -


def test_slugify_segment_empty_fallback():
    assert slugify_segment("") == "section"
    assert slugify_segment("---") == "section"


def test_slugify_path_preserves_directory_separator():
    # From docs/data-model.md Path conventions table.
    assert slugify_path("notes/My Projects/Big Idea.md") == "notes/my-projects/big-idea.md"


def test_slugify_path_preserves_underscore_filename():
    assert slugify_path("notes/projects/_index.md") == "notes/projects/_index.md"


def test_document_uri_uses_vault_id_prefix():
    vault_id = "7f3c8a4d-1234-5678-9abc-def012345678"
    uri = document_uri(vault_id, "notes/My Projects/Big Idea.md")
    assert uri == f"{vault_id}/notes/my-projects/big-idea.md"


def test_document_uri_with_pathlib_input():
    from pathlib import Path

    vault_id = "vault-1"
    uri = document_uri(vault_id, Path("ideas.md"))
    assert uri == f"{vault_id}/ideas.md"
