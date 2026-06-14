"""Slugification matches the docs/data-model/schema.md *Path conventions* examples."""

import pytest

from ki.vault import document_uri, folder_uri, slugify_path, slugify_segment


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
    # From docs/data-model/schema.md Path conventions table.
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


# --- folder_uri ------------------------------------------------------------


def test_folder_uri_single_segment():
    assert folder_uri("vault-1", ("notes",)) == "vault-1/notes"


def test_folder_uri_multi_segment():
    assert folder_uri("vault-1", ("a", "b", "c")) == "vault-1/a/b/c"


def test_folder_uri_slugifies_each_segment():
    # Same rules as Document.uri — each segment slugified independently.
    assert folder_uri("v", ("My Projects",)) == "v/my-projects"
    assert folder_uri("v", ("notes", "My Projects")) == "v/notes/my-projects"


def test_folder_uri_handles_special_chars_per_segment():
    assert folder_uri("v", ("Hello, World!",)) == "v/hello-world"


def test_folder_uri_accepts_list_segments():
    # Documented signature is tuple[str, ...] | list[str].
    assert folder_uri("v", ["notes", "ideas"]) == "v/notes/ideas"


def test_folder_uri_no_trailing_slash():
    # Folder.uri is a strict prefix of any Document.uri under it but never
    # ends in '/' — see docs/data-model/schema.md §Folder.
    assert not folder_uri("v", ("a", "b")).endswith("/")


def test_folder_uri_empty_segments_raises():
    with pytest.raises(ValueError, match="at least one path segment"):
        folder_uri("v", ())
    with pytest.raises(ValueError, match="at least one path segment"):
        folder_uri("v", [])
