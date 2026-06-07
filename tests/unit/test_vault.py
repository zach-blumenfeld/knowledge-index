"""Vault marker IO + slug rules: `.ki/vault.yaml` read/write, slug computation."""

import logging
import re

import pytest
import yaml

from ki.vault import (
    DESCRIPTION_MAX_BYTES,
    InvalidVaultBasenameError,
    VaultDescriptionExists,
    compute_base_slug,
    find_next_vault_slug,
    find_vault_root,
    read_vault_description,
    read_vault_marker,
    read_vault_profile,
    remove_vault_marker,
    vault_marker_path,
    write_vault_description,
    write_vault_marker,
)

# ---- read_vault_marker / write_vault_marker -------------------------------


def test_write_marker_round_trips_uri_only(tmp_path):
    write_vault_marker(tmp_path, uri="my-notes")
    data = yaml.safe_load(vault_marker_path(tmp_path).read_text(encoding="utf-8"))
    assert data == {"uri": "my-notes"}


def test_write_marker_round_trips_uri_and_description(tmp_path):
    write_vault_marker(tmp_path, uri="my-notes", description="long-form drafts")
    data = yaml.safe_load(vault_marker_path(tmp_path).read_text(encoding="utf-8"))
    assert data == {"uri": "my-notes", "description": "long-form drafts"}


def test_write_marker_round_trips_profile(tmp_path):
    write_vault_marker(tmp_path, uri="my-notes", profile="personal")
    data = yaml.safe_load(vault_marker_path(tmp_path).read_text(encoding="utf-8"))
    assert data == {"uri": "my-notes", "profile": "personal"}
    assert read_vault_profile(tmp_path) == "personal"


def test_read_profile_none_when_absent(tmp_path):
    write_vault_marker(tmp_path, uri="my-notes")
    assert read_vault_profile(tmp_path) is None


def test_read_profile_none_when_no_marker(tmp_path):
    assert read_vault_profile(tmp_path) is None


def test_write_description_preserves_profile(tmp_path):
    write_vault_marker(tmp_path, uri="my-notes", profile="work")
    write_vault_description(tmp_path, "drafts")
    assert read_vault_profile(tmp_path) == "work"
    assert read_vault_description(tmp_path) == "drafts"


def test_find_vault_root_walks_up_from_subdir(tmp_path):
    write_vault_marker(tmp_path, uri="my-notes")
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert find_vault_root(nested) == tmp_path.resolve()


def test_find_vault_root_walks_up_from_file(tmp_path):
    write_vault_marker(tmp_path, uri="my-notes")
    f = tmp_path / "a" / "note.md"
    f.parent.mkdir(parents=True)
    f.write_text("# hi", encoding="utf-8")
    assert find_vault_root(f) == tmp_path.resolve()


def test_find_vault_root_none_when_no_marker(tmp_path):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_vault_root(nested) is None


def test_write_marker_truncates_long_description(tmp_path, caplog):
    huge = "z" * (DESCRIPTION_MAX_BYTES + 100)
    with caplog.at_level(logging.WARNING, logger="ki.vault"):
        write_vault_marker(tmp_path, uri="my-notes", description=huge)
    on_disk = yaml.safe_load(vault_marker_path(tmp_path).read_text(encoding="utf-8"))
    assert len(on_disk["description"].encode("utf-8")) <= DESCRIPTION_MAX_BYTES
    assert any("truncating" in r.message for r in caplog.records)


def test_read_marker_returns_none_when_missing(tmp_path):
    assert read_vault_marker(tmp_path) is None


def test_read_marker_parses_existing(tmp_path):
    marker = vault_marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("uri: my-notes\ndescription: hello\n", encoding="utf-8")
    data = read_vault_marker(tmp_path)
    assert data == {"uri": "my-notes", "description": "hello"}


def test_read_marker_rejects_malformed_yaml(tmp_path):
    marker = vault_marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("uri: [not a string\n", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        read_vault_marker(tmp_path)


def test_read_marker_rejects_missing_uri(tmp_path):
    marker = vault_marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("description: just a description\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing a non-empty `uri:`"):
        read_vault_marker(tmp_path)


def test_remove_marker_idempotent(tmp_path):
    write_vault_marker(tmp_path, uri="my-notes")
    assert read_vault_marker(tmp_path) is not None
    remove_vault_marker(tmp_path)
    assert read_vault_marker(tmp_path) is None
    remove_vault_marker(tmp_path)  # no-op


def test_remove_marker_removes_empty_dir(tmp_path):
    write_vault_marker(tmp_path, uri="my-notes")
    remove_vault_marker(tmp_path)
    assert not (tmp_path / ".ki").exists()


# ---- compute_base_slug ----------------------------------------------------


def test_compute_base_slug_from_basename(tmp_path):
    d = tmp_path / "my-notes"
    d.mkdir()
    assert compute_base_slug(d) == "my-notes"


def test_compute_base_slug_normalizes_basename(tmp_path):
    d = tmp_path / "My Knowledge Base"
    d.mkdir()
    assert compute_base_slug(d) == "my-knowledge-base"


def test_compute_base_slug_strips_unicode(tmp_path):
    d = tmp_path / "Café Notes"
    d.mkdir()
    assert compute_base_slug(d) == "cafe-notes"


def test_compute_base_slug_refuses_useless_basename(tmp_path):
    """A folder name that slugifies to empty should error with a clear msg."""
    d = tmp_path / "___"
    d.mkdir()
    with pytest.raises(InvalidVaultBasenameError) as excinfo:
        compute_base_slug(d)
    msg = str(excinfo.value)
    assert "___" in msg
    # Must include guidance, not just the failure.
    assert "rename" in msg.lower()


# ---- find_next_vault_slug (algorithm — DB-free, fake session) -------------


class _FakeSession:
    """Minimal stand-in for a Neo4j session — returns the rows we hand it."""

    def __init__(self, uris: list[str]):
        self._uris = uris

    def run(self, _query: str, **_params):
        rows = [{"uri": u} for u in self._uris]
        # Mimic the iterator interface `list(session.run(...))` uses.
        return iter(rows)


def test_find_next_slug_uses_base_when_family_empty():
    s = _FakeSession(uris=[])
    assert find_next_vault_slug(s, "my-notes") == "my-notes"


def test_find_next_slug_first_collision_yields_dash_1():
    s = _FakeSession(uris=["my-notes"])
    assert find_next_vault_slug(s, "my-notes") == "my-notes-1"


def test_find_next_slug_increments_from_max():
    s = _FakeSession(uris=["my-notes", "my-notes-1", "my-notes-2"])
    assert find_next_vault_slug(s, "my-notes") == "my-notes-3"


def test_find_next_slug_uses_max_plus_one_with_gaps_present():
    """Max+1 over present slugs — `-3` is max, so next is `-4` even with a `-2` gap.

    The "gap" case (e.g. `-2` missing) is unit-test-only because at the
    integration level a missing `-2` could just as easily mean `-2` was
    deleted; reuse vs. non-reuse depends on whether `-3` is also gone.
    """
    s = _FakeSession(uris=["my-notes", "my-notes-1", "my-notes-3"])
    assert find_next_vault_slug(s, "my-notes") == "my-notes-4"


def test_find_next_slug_ignores_lookalike_slugs():
    """`my-notes-x-1` doesn't match the `my-notes(-N)?` pattern; should be ignored."""
    s = _FakeSession(uris=["my-notes-x", "my-notes-x-1", "my-notes-extra"])
    # None of those are in the my-notes family — base is free.
    assert find_next_vault_slug(s, "my-notes") == "my-notes"


def test_find_next_slug_treats_base_as_suffix_zero():
    """When `my-notes` itself is present, it counts as suffix 0 — next is -1."""
    s = _FakeSession(uris=["my-notes"])
    assert find_next_vault_slug(s, "my-notes") == "my-notes-1"


def test_find_next_slug_handles_regex_special_chars():
    """Base slugs follow the slug-segment rules so this is mostly defensive,
    but `re.escape` should still cover any future relaxation."""
    s = _FakeSession(uris=["my.notes", "my.notes-1"])
    assert find_next_vault_slug(s, "my.notes") == "my.notes-2"


# ---- write_vault_description (still in use; round-trip) -------------------


def test_write_description_round_trips(tmp_path):
    write_vault_marker(tmp_path, uri="my-notes")
    write_vault_description(tmp_path, "graph database research notes")
    data = yaml.safe_load(vault_marker_path(tmp_path).read_text(encoding="utf-8"))
    assert data["uri"] == "my-notes"
    assert data["description"] == "graph database research notes"


def test_write_description_raises_when_present_without_force(tmp_path):
    marker = vault_marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("uri: my-notes\ndescription: original\n", encoding="utf-8")
    with pytest.raises(VaultDescriptionExists) as exc_info:
        write_vault_description(tmp_path, "replacement")
    assert exc_info.value.existing == "original"


def test_write_description_overwrites_with_force(tmp_path):
    marker = vault_marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("uri: my-notes\ndescription: original\n", encoding="utf-8")
    write_vault_description(tmp_path, "replacement", force=True)
    data = yaml.safe_load(marker.read_text(encoding="utf-8"))
    assert data["description"] == "replacement"


def test_write_description_raises_when_marker_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        write_vault_description(tmp_path, "no marker yet")


def test_read_description_returns_field(tmp_path):
    write_vault_marker(tmp_path, uri="my-notes", description="hello")
    desc = read_vault_description(tmp_path)
    assert desc == "hello"


def test_read_description_returns_none_when_absent(tmp_path):
    write_vault_marker(tmp_path, uri="my-notes")
    assert read_vault_description(tmp_path) is None


def test_read_description_ignores_non_string_value(tmp_path, caplog):
    marker = vault_marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("uri: my-notes\ndescription: 42\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="ki.vault"):
        assert read_vault_description(tmp_path) is None
    assert any("should be a string" in r.message for r in caplog.records)


# ---- Slug validation regex (defensive) ------------------------------------


def test_assigned_slugs_match_slug_rules():
    """The slugs we assign should always pass the standard slug-segment regex."""
    pattern = re.compile(r"^[a-z0-9_.-]+(?:-\d+)?$")
    s = _FakeSession(uris=["my-notes", "my-notes-2"])
    next_slug = find_next_vault_slug(s, "my-notes")
    assert pattern.match(next_slug)
