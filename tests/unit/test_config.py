"""Config loader: XDG paths, profile lookup, env-var override, 0600 mode."""

import os
import stat

import pytest

from ki.config import (
    PROFILE_ENV_VAR,
    Config,
    Profile,
    default_config_path,
    fallback_config_path,
    find_config_path,
    load_config,
    save_config,
)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Redirect HOME and XDG_CONFIG_HOME into tmp_path so we don't touch the real config."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv(PROFILE_ENV_VAR, raising=False)
    return tmp_path


def test_default_path_follows_xdg(isolated_home):
    assert default_config_path() == isolated_home / "xdg" / "ki" / "config.yaml"


def test_default_path_fallback_when_xdg_unset(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert default_config_path() == tmp_path / ".config" / "ki" / "config.yaml"


def test_save_then_load_roundtrip(isolated_home):
    cfg = Config()
    cfg.add_profile(Profile(
        name="default", uri="bolt://localhost:7687",
        user="neo4j", password="hunter2", source="local-podman",
    ))
    path = save_config(cfg)
    loaded = load_config(path)
    assert "default" in loaded.profiles
    assert loaded.profiles["default"].uri == "bolt://localhost:7687"


def test_save_writes_0600_mode(isolated_home):
    cfg = Config()
    cfg.add_profile(Profile(
        name="default", uri="bolt://localhost:7687", user="neo4j", password="x",
    ))
    path = save_config(cfg)
    mode = os.stat(path).st_mode & 0o777
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"
    # Sanity: owner has rw, group/world have nothing
    assert mode & stat.S_IRGRP == 0
    assert mode & stat.S_IROTH == 0


def test_get_profile_is_pure_by_name_lookup(isolated_home, monkeypatch):
    # get_profile looks up by exact name only — no env var, no default, no
    # sole-profile auto-pick (that precedence lives in resolve_profile).
    cfg = Config()
    cfg.add_profile(Profile(name="only", uri="u1", user="u", password="p"))
    monkeypatch.setenv(PROFILE_ENV_VAR, "only")
    assert cfg.get_profile("only").name == "only"
    with pytest.raises(KeyError):
        cfg.get_profile("")  # empty never resolves, even with KI_PROFILE set


def test_get_profile_raises_when_unknown(isolated_home):
    cfg = Config()
    cfg.add_profile(Profile(name="default", uri="u1", user="u", password="p"))
    with pytest.raises(KeyError):
        cfg.get_profile("missing")


def test_find_config_path_xdg_first(isolated_home):
    # Neither exists yet
    assert find_config_path() is None
    # Create XDG file
    primary = default_config_path()
    primary.parent.mkdir(parents=True, exist_ok=True)
    primary.write_text("profiles: {}\n")
    assert find_config_path() == primary


def test_find_config_path_falls_back(isolated_home):
    # Only the fallback exists
    fb = fallback_config_path()
    fb.parent.mkdir(parents=True, exist_ok=True)
    fb.write_text("profiles: {}\n")
    assert find_config_path() == fb
