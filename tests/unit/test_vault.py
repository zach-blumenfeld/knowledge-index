"""Vault marker IO: read-if-exists, write-if-missing, idempotent."""

import uuid

from ki.vault import (
    read_or_create_vault_id,
    read_vault_id,
    remove_vault_marker,
    vault_marker_path,
)


def test_read_or_create_writes_a_uuid(tmp_path):
    vault_id, created = read_or_create_vault_id(tmp_path)
    assert created is True
    assert vault_marker_path(tmp_path).exists()
    # Valid UUID v4
    parsed = uuid.UUID(vault_id)
    assert parsed.version == 4


def test_read_or_create_is_idempotent(tmp_path):
    first, _ = read_or_create_vault_id(tmp_path)
    second, created2 = read_or_create_vault_id(tmp_path)
    assert first == second
    assert created2 is False


def test_read_vault_id_returns_none_when_missing(tmp_path):
    assert read_vault_id(tmp_path) is None


def test_remove_vault_marker_idempotent(tmp_path):
    vault_id, _ = read_or_create_vault_id(tmp_path)
    assert read_vault_id(tmp_path) == vault_id
    remove_vault_marker(tmp_path)
    assert read_vault_id(tmp_path) is None
    # second call is a no-op
    remove_vault_marker(tmp_path)


def test_remove_marker_removes_empty_dir(tmp_path):
    read_or_create_vault_id(tmp_path)
    remove_vault_marker(tmp_path)
    assert not (tmp_path / ".ki").exists()
