"""Unit tests for the :Folder layer's row-building helper.

`_build_folder_and_tree_rows` is the pure-Python function that translates a
list of doc paths into (folder_rows, tree_edge_rows) — what the ingest
pipeline ships to Neo4j as `WRITE_FOLDERS` / `WRITE_TREE_EDGES` payloads.
"""

from pathlib import Path

from ki.ingest.pipeline import _build_folder_and_tree_rows
from ki.vault import document_uri, folder_uri

VAULT = "v-1"


def _doc_paths(root: Path, rel_paths: list[str]) -> list[Path]:
    """Create empty .md files at the given paths under `root` and return their absolute Paths."""
    out: list[Path] = []
    for rel in rel_paths:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# stub\n")
        out.append(p)
    return out


def test_empty_doc_list_returns_empty(tmp_path):
    folders, edges = _build_folder_and_tree_rows(VAULT, tmp_path, [])
    assert folders == []
    assert edges == []


def test_root_only_docs_create_no_folders(tmp_path):
    paths = _doc_paths(tmp_path, ["a.md", "b.md", "c.md"])
    folders, edges = _build_folder_and_tree_rows(VAULT, tmp_path, paths)
    assert folders == []
    assert len(edges) == 3
    for e in edges:
        assert e["parentUri"] == VAULT
    child_uris = {e["childUri"] for e in edges}
    assert child_uris == {
        document_uri(VAULT, "a.md"),
        document_uri(VAULT, "b.md"),
        document_uri(VAULT, "c.md"),
    }


def test_single_nested_doc_creates_full_folder_chain(tmp_path):
    paths = _doc_paths(tmp_path, ["a/b/c/leaf.md"])
    folders, edges = _build_folder_and_tree_rows(VAULT, tmp_path, paths)

    expected_folder_uris = {
        folder_uri(VAULT, ("a",)),
        folder_uri(VAULT, ("a", "b")),
        folder_uri(VAULT, ("a", "b", "c")),
    }
    assert {f["uri"] for f in folders} == expected_folder_uris

    expected_edges = {
        (VAULT, folder_uri(VAULT, ("a",))),
        (folder_uri(VAULT, ("a",)), folder_uri(VAULT, ("a", "b"))),
        (folder_uri(VAULT, ("a", "b")), folder_uri(VAULT, ("a", "b", "c"))),
        (folder_uri(VAULT, ("a", "b", "c")), document_uri(VAULT, "a/b/c/leaf.md")),
    }
    actual_edges = {(e["parentUri"], e["childUri"]) for e in edges}
    assert actual_edges == expected_edges


def test_folder_shared_by_siblings_is_emitted_once(tmp_path):
    paths = _doc_paths(tmp_path, ["notes/one.md", "notes/two.md", "notes/three.md"])
    folders, edges = _build_folder_and_tree_rows(VAULT, tmp_path, paths)

    # Just one folder — `notes` — even though three docs live in it.
    assert len(folders) == 1
    assert folders[0]["uri"] == folder_uri(VAULT, ("notes",))

    # One Vault→Folder edge plus three Folder→Document edges. No duplicates.
    assert len(edges) == 4
    parents = [(e["parentUri"], e["childUri"]) for e in edges]
    assert parents.count((VAULT, folder_uri(VAULT, ("notes",)))) == 1


def test_sibling_folders_share_common_ancestor(tmp_path):
    paths = _doc_paths(
        tmp_path,
        [
            "notes/projects/alpha.md",
            "notes/projects/beta.md",
            "notes/archive/old.md",
        ],
    )
    folders, edges = _build_folder_and_tree_rows(VAULT, tmp_path, paths)

    folder_uris = {f["uri"] for f in folders}
    assert folder_uris == {
        folder_uri(VAULT, ("notes",)),
        folder_uri(VAULT, ("notes", "projects")),
        folder_uri(VAULT, ("notes", "archive")),
    }

    # Folder→Folder edges from `notes` to its two children, plus the
    # Vault→`notes` edge. No duplicate Vault→`notes` edge even though
    # multiple docs reach down through it.
    edge_pairs = [(e["parentUri"], e["childUri"]) for e in edges]
    assert edge_pairs.count((VAULT, folder_uri(VAULT, ("notes",)))) == 1
    assert (
        folder_uri(VAULT, ("notes",)),
        folder_uri(VAULT, ("notes", "projects")),
    ) in edge_pairs
    assert (
        folder_uri(VAULT, ("notes",)),
        folder_uri(VAULT, ("notes", "archive")),
    ) in edge_pairs


def test_mixed_root_and_nested_docs(tmp_path):
    paths = _doc_paths(
        tmp_path,
        ["root.md", "a/one.md", "a/b/two.md", "x/y/z/deep.md"],
    )
    folders, edges = _build_folder_and_tree_rows(VAULT, tmp_path, paths)

    folder_uris = {f["uri"] for f in folders}
    assert folder_uris == {
        folder_uri(VAULT, ("a",)),
        folder_uri(VAULT, ("a", "b")),
        folder_uri(VAULT, ("x",)),
        folder_uri(VAULT, ("x", "y")),
        folder_uri(VAULT, ("x", "y", "z")),
    }

    # Root doc gets Vault→Document directly.
    assert {"parentUri": VAULT, "childUri": document_uri(VAULT, "root.md")} in edges


def test_single_parent_invariant_every_node_has_one_inbound_edge(tmp_path):
    paths = _doc_paths(
        tmp_path,
        [
            "root.md",
            "a/x.md",
            "a/b/y.md",
            "a/b/z.md",  # sibling of y.md
            "a/c/q.md",  # second branch from a/
            "deep/path/very/nested/file.md",
        ],
    )
    folders, edges = _build_folder_and_tree_rows(VAULT, tmp_path, paths)

    # Build the set of all child URIs across folders + documents.
    child_uris = {e["childUri"] for e in edges}
    folder_uris = {f["uri"] for f in folders}
    doc_uris = {document_uri(VAULT, p.relative_to(tmp_path)) for p in paths}

    # Every Folder and every Document must appear exactly once as a child.
    incoming_counts: dict[str, int] = {}
    for e in edges:
        incoming_counts[e["childUri"]] = incoming_counts.get(e["childUri"], 0) + 1
    for uri in folder_uris | doc_uris:
        assert incoming_counts.get(uri, 0) == 1, (
            f"single-parent invariant violated for {uri}: "
            f"got {incoming_counts.get(uri, 0)} incoming edges"
        )

    # And no spurious children show up.
    assert child_uris == folder_uris | doc_uris


def test_folder_display_name_keeps_original_casing(tmp_path):
    """Folder.displayName preserves the on-disk casing; only `name` is slugified."""
    paths = _doc_paths(tmp_path, ["My Projects/Big Idea.md"])
    folders, _ = _build_folder_and_tree_rows(VAULT, tmp_path, paths)

    assert len(folders) == 1
    props = folders[0]["props"]
    assert props["displayName"] == "My Projects"
    assert props["name"] == "my-projects"


def test_re_run_with_same_paths_is_deterministic(tmp_path):
    """Same inputs → same outputs (incl. ordering of edges) so MERGE is idempotent."""
    paths = _doc_paths(tmp_path, ["a/b.md", "a/c.md", "d.md"])
    out1 = _build_folder_and_tree_rows(VAULT, tmp_path, paths)
    out2 = _build_folder_and_tree_rows(VAULT, tmp_path, paths)
    assert out1 == out2
