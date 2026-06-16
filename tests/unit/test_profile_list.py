"""`ki profile list` — config-only profile enumeration."""

from __future__ import annotations

import json

import pytest
from click import ClickException

from ki.commands import profile as P
from ki.config import Config, Profile, save_config


def _write_cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("KI_PROFILE", raising=False)
    cfg = Config()
    cfg.add_profile(Profile(name="personal", uri="bolt://p", user="neo4j", password="x"))
    cfg.add_profile(Profile(
        name="work", uri="neo4j+s://w", user="neo4j", password="x",
        source="aura", database="dbid123",
    ))
    save_config(cfg)


def test_lists_all_profiles_json(tmp_path, monkeypatch, capsys):
    _write_cfg(tmp_path, monkeypatch)
    rc = P.cmd_profile_list(as_json=True)
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    by_name = {r["name"]: r for r in rows}
    assert set(by_name) == {"personal", "work"}
    assert "default" not in by_name["personal"]  # no default-profile concept
    assert by_name["work"]["database"] == "dbid123"
    assert by_name["personal"]["database"] is None  # home database


def test_empty_config_is_not_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    rc = P.cmd_profile_list(as_json=True)
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


# ---- profile sync (neo4j-cli credential bridge) ----------------------------


def test_profile_sync_errors_without_neo4j_cli(tmp_path, monkeypatch):
    _write_cfg(tmp_path, monkeypatch)
    monkeypatch.setattr("ki.neo4j_cli.is_available", lambda: False)
    with pytest.raises(ClickException) as e:
        P.cmd_profile_sync()
    assert "neo4j-cli" in str(e.value)


def test_profile_sync_registers_all_profiles(tmp_path, monkeypatch):
    _write_cfg(tmp_path, monkeypatch)
    monkeypatch.setattr("ki.neo4j_cli.is_available", lambda: True)
    registered: list[str] = []
    monkeypatch.setattr(
        "ki.neo4j_cli.register_credential", lambda p: registered.append(p.name)
    )
    rc = P.cmd_profile_sync()
    assert rc == 0
    assert set(registered) == {"personal", "work"}


def test_profile_sync_one_profile(tmp_path, monkeypatch):
    _write_cfg(tmp_path, monkeypatch)
    monkeypatch.setattr("ki.neo4j_cli.is_available", lambda: True)
    registered: list[str] = []
    monkeypatch.setattr(
        "ki.neo4j_cli.register_credential", lambda p: registered.append(p.name)
    )
    rc = P.cmd_profile_sync("work")
    assert rc == 0
    assert registered == ["work"]


def test_profile_sync_unknown_profile_errors(tmp_path, monkeypatch):
    _write_cfg(tmp_path, monkeypatch)
    monkeypatch.setattr("ki.neo4j_cli.is_available", lambda: True)
    monkeypatch.setattr("ki.neo4j_cli.register_credential", lambda p: None)
    with pytest.raises(ClickException):
        P.cmd_profile_sync("ghost")
