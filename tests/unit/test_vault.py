"""Vault marker IO: `.ki/vault.yaml` create/read, description handling."""

import logging
import uuid

import pytest
import yaml

from ki.vault import (
    DESCRIPTION_MAX_BYTES,
    VaultDescriptionExists,
    read_or_create_vault_id,
    read_vault_description,
    read_vault_id,
    remove_vault_marker,
    vault_marker_path,
    write_vault_description,
)


def test_read_or_create_writes_yaml_with_uuid(tmp_path):
    vault_id, created = read_or_create_vault_id(tmp_path)
    assert created is True
    marker = vault_marker_path(tmp_path)
    assert marker.exists()
    assert marker.name == "vault.yaml"
    # Valid UUID v4 in the YAML.
    parsed = uuid.UUID(vault_id)
    assert parsed.version == 4
    on_disk = yaml.safe_load(marker.read_text(encoding="utf-8"))
    assert on_disk == {"uri": vault_id}


def test_read_or_create_is_idempotent(tmp_path):
    first, _ = read_or_create_vault_id(tmp_path)
    second, created2 = read_or_create_vault_id(tmp_path)
    assert first == second
    assert created2 is False


def test_read_or_create_preserves_existing_user_fields(tmp_path):
    """A user-authored description must survive a re-read by `ki index`."""
    marker = vault_marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        "uri: 550e8400-e29b-41d4-a716-446655440000\ndescription: hello\n",
        encoding="utf-8",
    )
    vault_id, created = read_or_create_vault_id(tmp_path)
    assert vault_id == "550e8400-e29b-41d4-a716-446655440000"
    assert created is False
    # File must be untouched (ki is read-only w.r.t. user fields).
    on_disk = yaml.safe_load(marker.read_text(encoding="utf-8"))
    assert on_disk["description"] == "hello"


def test_read_or_create_rejects_malformed_yaml(tmp_path):
    marker = vault_marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("uri: [not a string\n", encoding="utf-8")  # malformed
    with pytest.raises(ValueError, match="malformed"):
        read_or_create_vault_id(tmp_path)


def test_read_or_create_rejects_missing_uri(tmp_path):
    marker = vault_marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("description: just a description\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing a non-empty `uri:`"):
        read_or_create_vault_id(tmp_path)


def test_read_vault_id_returns_none_when_missing(tmp_path):
    assert read_vault_id(tmp_path) is None


def test_remove_vault_marker_idempotent(tmp_path):
    vault_id, _ = read_or_create_vault_id(tmp_path)
    assert read_vault_id(tmp_path) == vault_id
    remove_vault_marker(tmp_path)
    assert read_vault_id(tmp_path) is None
    remove_vault_marker(tmp_path)  # no-op


def test_remove_marker_removes_empty_dir(tmp_path):
    read_or_create_vault_id(tmp_path)
    remove_vault_marker(tmp_path)
    assert not (tmp_path / ".ki").exists()


def test_read_vault_description_returns_field(tmp_path):
    marker = vault_marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        "uri: 550e8400-e29b-41d4-a716-446655440000\n"
        "description: |\n"
        "  Notes on graph databases.\n"
        "  Use this for Neo4j / Cypher questions.\n",
        encoding="utf-8",
    )
    desc = read_vault_description(tmp_path)
    assert desc is not None
    assert "graph databases" in desc
    assert "Cypher" in desc


def test_read_vault_description_returns_none_when_absent(tmp_path):
    read_or_create_vault_id(tmp_path)  # writes only `uri:`
    assert read_vault_description(tmp_path) is None


def test_read_vault_description_returns_none_when_marker_missing(tmp_path):
    assert read_vault_description(tmp_path) is None


def test_read_vault_description_returns_none_on_empty_string(tmp_path):
    marker = vault_marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        "uri: 550e8400-e29b-41d4-a716-446655440000\ndescription: '   '\n",
        encoding="utf-8",
    )
    assert read_vault_description(tmp_path) is None


def test_read_vault_description_truncates_at_8kb_with_warning(tmp_path, caplog):
    huge = "x" * (DESCRIPTION_MAX_BYTES + 1024)
    marker = vault_marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    yaml_text = yaml.safe_dump(
        {"uri": "550e8400-e29b-41d4-a716-446655440000", "description": huge},
        sort_keys=False,
    )
    marker.write_text(yaml_text, encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="ki.vault"):
        desc = read_vault_description(tmp_path)

    assert desc is not None
    assert len(desc.encode("utf-8")) <= DESCRIPTION_MAX_BYTES
    assert any("truncating" in r.message for r in caplog.records)


def test_write_vault_description_writes_when_missing(tmp_path):
    vault_id, _ = read_or_create_vault_id(tmp_path)
    write_vault_description(tmp_path, "graph database research notes")
    data = yaml.safe_load(vault_marker_path(tmp_path).read_text(encoding="utf-8"))
    assert data["uri"] == vault_id
    assert data["description"] == "graph database research notes"


def test_write_vault_description_preserves_uri_and_unknown_fields(tmp_path):
    marker = vault_marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        "uri: 550e8400-e29b-41d4-a716-446655440000\n"
        "future_field: still here\n",
        encoding="utf-8",
    )
    write_vault_description(tmp_path, "hello")
    data = yaml.safe_load(marker.read_text(encoding="utf-8"))
    assert data["uri"] == "550e8400-e29b-41d4-a716-446655440000"
    assert data["future_field"] == "still here"
    assert data["description"] == "hello"


def test_write_vault_description_raises_when_present_without_force(tmp_path):
    marker = vault_marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        "uri: 550e8400-e29b-41d4-a716-446655440000\ndescription: original\n",
        encoding="utf-8",
    )
    with pytest.raises(VaultDescriptionExists) as exc_info:
        write_vault_description(tmp_path, "replacement")
    assert exc_info.value.existing == "original"
    # File untouched.
    assert "original" in marker.read_text(encoding="utf-8")


def test_write_vault_description_overwrites_with_force(tmp_path):
    marker = vault_marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        "uri: 550e8400-e29b-41d4-a716-446655440000\ndescription: original\n",
        encoding="utf-8",
    )
    write_vault_description(tmp_path, "replacement", force=True)
    data = yaml.safe_load(marker.read_text(encoding="utf-8"))
    assert data["description"] == "replacement"


def test_write_vault_description_empty_existing_treated_as_unset(tmp_path):
    """Whitespace-only description should not block a non-force write."""
    marker = vault_marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        "uri: 550e8400-e29b-41d4-a716-446655440000\ndescription: '   '\n",
        encoding="utf-8",
    )
    write_vault_description(tmp_path, "real description")  # no force needed
    data = yaml.safe_load(marker.read_text(encoding="utf-8"))
    assert data["description"] == "real description"


def test_write_vault_description_truncates_at_8kb(tmp_path, caplog):
    read_or_create_vault_id(tmp_path)
    huge = "y" * (DESCRIPTION_MAX_BYTES + 100)
    with caplog.at_level(logging.WARNING, logger="ki.vault"):
        write_vault_description(tmp_path, huge)
    desc = read_vault_description(tmp_path)
    assert desc is not None
    assert len(desc.encode("utf-8")) <= DESCRIPTION_MAX_BYTES
    assert any("truncating" in r.message for r in caplog.records)


def test_write_vault_description_raises_when_marker_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        write_vault_description(tmp_path, "no marker yet")


def test_read_vault_description_ignores_non_string_value(tmp_path, caplog):
    marker = vault_marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        "uri: 550e8400-e29b-41d4-a716-446655440000\ndescription: 42\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="ki.vault"):
        assert read_vault_description(tmp_path) is None
    assert any("should be a string" in r.message for r in caplog.records)
