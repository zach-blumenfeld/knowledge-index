"""`ki status` engine: STALE two-tier diff + the no-Neo4j layers."""

from __future__ import annotations

from pathlib import Path

from ki import status as S
from ki.config import Config, Profile
from ki.parser.markdown import hash_bytes
from ki.vault import write_vault_marker


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Dispatches the two status queries by a substring of their text."""

    def __init__(self, exists: bool, doc_hashes: dict[str, str]):
        self._exists = exists
        self._doc_hashes = doc_hashes

    def run(self, query, **kw):
        if "count(v)" in query:
            return _FakeResult([{"n": 1 if self._exists else 0}])
        if "LOCAL_FILE" in query:
            return _FakeResult(
                [{"uri": u, "fileHash": h} for u, h in self._doc_hashes.items()]
            )
        raise AssertionError(f"unexpected query: {query}")


def _make_vault(tmp_path, files: dict[str, bytes]) -> dict[str, str]:
    """Write a marker + md files; return the expected {doc_uri: hash} for disk."""
    write_vault_marker(tmp_path, uri="v", profile="p")
    out: dict[str, str] = {}
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        out[f"v/{rel}"] = hash_bytes(content)
    return out


# ---- graph_state: NOT_INDEXED / READY / STALE -----------------------------


def test_not_indexed_when_vault_absent(tmp_path):
    _make_vault(tmp_path, {"a.md": b"# A"})
    state, _ = S.graph_state(_FakeSession(exists=False, doc_hashes={}), tmp_path, "v")
    assert state == S.NOT_INDEXED


def test_ready_when_sets_and_hashes_match(tmp_path):
    disk = _make_vault(tmp_path, {"a.md": b"# A", "notes/b.md": b"# B"})
    state, _ = S.graph_state(_FakeSession(True, disk), tmp_path, "v")
    assert state == S.READY


def test_stale_added_file_on_disk(tmp_path):
    disk = _make_vault(tmp_path, {"a.md": b"# A", "b.md": b"# B"})
    graph = {"v/a.md": disk["v/a.md"]}  # b.md never indexed
    state, detail = S.graph_state(_FakeSession(True, graph), tmp_path, "v")
    assert state == S.STALE
    assert detail["added"] == 1 and detail["removed"] == 0


def test_stale_removed_from_disk(tmp_path):
    disk = _make_vault(tmp_path, {"a.md": b"# A"})
    graph = {**disk, "v/gone.md": "deadbeef"}  # graph has an extra doc
    state, detail = S.graph_state(_FakeSession(True, graph), tmp_path, "v")
    assert state == S.STALE
    assert detail["removed"] == 1


def test_stale_changed_content(tmp_path):
    _make_vault(tmp_path, {"a.md": b"# A"})
    graph = {"v/a.md": "differenthash"}  # same uri, stale hash
    state, detail = S.graph_state(_FakeSession(True, graph), tmp_path, "v")
    assert state == S.STALE
    assert detail["changed"] == 1


def test_set_check_short_circuits_before_hashing(tmp_path):
    # An added file should be caught by the set check — changed stays 0 even
    # though we never hash.
    _make_vault(tmp_path, {"a.md": b"# A", "b.md": b"# B"})
    graph = {"v/a.md": "wrong-hash-but-irrelevant"}
    state, detail = S.graph_state(_FakeSession(True, graph), tmp_path, "v")
    assert state == S.STALE
    assert detail["changed"] == 0  # short-circuited on the set diff


def test_stale_detail_carries_uri_lists(tmp_path):
    """Detail includes the actual out-of-sync uris (for `ki status -v` / --json),
    not just counts."""
    disk = _make_vault(tmp_path, {"a.md": b"# A", "b.md": b"# B"})
    graph = {"v/a.md": disk["v/a.md"]}  # b.md added on disk
    _, detail = S.graph_state(_FakeSession(True, graph), tmp_path, "v")
    assert detail["added_uris"] == ["v/b.md"]
    assert detail["removed_uris"] == []
    assert detail["changed_uris"] == []


def test_stale_changed_detail_lists_uris(tmp_path):
    _make_vault(tmp_path, {"a.md": b"# A"})
    graph = {"v/a.md": "differenthash"}
    _, detail = S.graph_state(_FakeSession(True, graph), tmp_path, "v")
    assert detail["changed_uris"] == ["v/a.md"]


# ---- STALE message + -v rendering -----------------------------------------


def test_stale_action_does_not_advise_ki_index():
    from ki.commands.status import _action

    r = S.StatusResult(
        state=S.STALE, path=Path("/x"),
        detail={"added": 1, "removed": 0, "changed": 2,
                "added_uris": ["v/new.md"], "removed_uris": [],
                "changed_uris": ["v/a.md", "v/b.md"]},
    )
    action = _action(r)
    assert "ki index" not in action  # no cwd-relative re-index prescription
    assert "1 added" in action and "2 changed" in action


def test_render_verbose_lists_stale_files(capsys):
    from ki.commands.status import _render

    r = S.StatusResult(
        state=S.STALE, path=Path("/x"), vault_uri="v",
        detail={"added": 1, "removed": 1, "changed": 0,
                "added_uris": ["v/new.md"], "removed_uris": ["v/gone.md"],
                "changed_uris": []},
    )
    _render(r, verbose=True)
    out = capsys.readouterr().out
    assert "v/new.md" in out and "v/gone.md" in out


def test_render_non_verbose_hints_at_v(capsys):
    from ki.commands.status import _render

    r = S.StatusResult(
        state=S.STALE, path=Path("/x"), vault_uri="v",
        detail={"added": 1, "removed": 0, "changed": 0,
                "added_uris": ["v/new.md"], "removed_uris": [], "changed_uris": []},
    )
    _render(r, verbose=False)
    out = capsys.readouterr().out
    assert "v/new.md" not in out          # files not listed without -v
    assert "-v" in out                    # but hinted


# ---- compute_status: layers that need no Neo4j ----------------------------


def test_not_a_vault(tmp_path):
    sub = tmp_path / "nowhere"
    sub.mkdir()
    cfg = Config()
    res = S.compute_status(cfg, sub)
    assert res.state == S.NOT_A_VAULT


def test_profile_missing(tmp_path):
    write_vault_marker(tmp_path, uri="v", profile="ghost")
    cfg = Config()
    cfg.add_profile(Profile(name="real", uri="bolt://x", user="neo4j", password="x"))
    res = S.compute_status(cfg, tmp_path)
    assert res.state == S.PROFILE_MISSING
    assert res.profile == "ghost"
    assert "ghost" in res.message
