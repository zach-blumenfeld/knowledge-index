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


# --- non-ASCII path segments (unique=True) ---------------------------------
#
# The URI is a MERGE key, so distinct files must never slugify to the same one.
# ASCII-folding used to drop the whole stem while the extension survived, so
# every non-ASCII sibling collapsed onto e.g. `.md` and was absorbed.


def test_non_ascii_path_segments_stay_distinct():
    a = slugify_segment("高精度运算.md", unique=True)
    b = slugify_segment("最大区间异或值.md", unique=True)
    c = slugify_segment("末班车.md", unique=True)
    assert len({a, b, c}) == 3, f"distinct filenames collided: {a} {b} {c}"


def test_names_differing_only_in_non_ascii_stay_distinct():
    # Same ASCII skeleton, different CJK: ASCII-folding made these identical.
    a = slugify_segment("GESP八级教辅资料_编程题冲刺（26.04.07）.md", unique=True)
    b = slugify_segment("GESP八级教辅资料_选择判断冲刺（26.04.07）.md", unique=True)
    assert a != b


def test_non_ascii_path_segment_keeps_readable_characters():
    # URIs are documented as readable and quotable, so CJK survives.
    assert slugify_segment("高精度运算.md", unique=True) == "高精度运算.md"
    assert slugify_segment("KMP算法.md", unique=True) == "kmp算法.md"


def test_ascii_path_segments_are_unchanged():
    assert slugify_segment("Big Idea.md", unique=True) == "big-idea.md"
    assert slugify_segment("_index.md", unique=True) == "_index.md"
    assert slugify_segment("Hello, World!", unique=True) == "hello-world"
    assert slugify_path("notes/My Projects/Big Idea.md") == "notes/my-projects/big-idea.md"
    assert document_uri("v", "notes/My Projects/Big Idea.md") == "v/notes/my-projects/big-idea.md"


def test_combining_marks_are_dropped_for_path_segments():
    assert slugify_segment("café.md", unique=True) == "cafe.md"


def test_same_basename_in_different_folders_yields_distinct_uris():
    assert document_uri("v", "a/末班车.md") != document_uri("v", "b/末班车.md")


def test_heading_segments_keep_section_sentinel():
    # unique=False (default) — headings are not MERGE keys on their own and rely
    # on the sentinel plus the caller's `-N` numbering.
    assert slugify_segment("定义") == "section"
    assert slugify_segment("") == "section"
    assert slugify_segment("---") == "section"


def test_folder_uri_non_ascii_segments_stay_distinct():
    assert folder_uri("v", ("教材",)) != folder_uri("v", ("课堂笔记",))
    assert folder_uri("v", ("教材",)) == "v/教材"


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
