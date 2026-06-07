"""`ki profile list` — config-only profile enumeration."""

from __future__ import annotations

import json

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
    cfg.default_profile = "personal"
    save_config(cfg)


def test_lists_all_profiles_json(tmp_path, monkeypatch, capsys):
    _write_cfg(tmp_path, monkeypatch)
    rc = P.cmd_profile_list(as_json=True)
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    by_name = {r["name"]: r for r in rows}
    assert set(by_name) == {"personal", "work"}
    assert by_name["personal"]["default"] is True
    assert by_name["work"]["default"] is False
    assert by_name["work"]["database"] == "dbid123"
    assert by_name["personal"]["database"] is None  # home database


def test_empty_config_is_not_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    rc = P.cmd_profile_list(as_json=True)
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []
