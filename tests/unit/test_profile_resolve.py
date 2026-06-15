"""Profile resolution precedence: --profile > vault binding > $KI_PROFILE > error.

No default profile, ever, and no sole-profile auto-pick (see scoping.md §4).
"""

import pytest
from click import ClickException

from ki.config import Config, Profile
from ki.profile_resolve import BoundProfileMissing, resolve_profile
from ki.vault import write_vault_marker


def _cfg():
    cfg = Config()
    cfg.add_profile(Profile(name="personal", uri="bolt://p", user="neo4j", password="x"))
    cfg.add_profile(Profile(name="work", uri="bolt://w", user="neo4j", password="x"))
    return cfg


@pytest.fixture(autouse=True)
def _no_ki_profile(monkeypatch):
    """Isolate from a dev shell that exports KI_PROFILE."""
    monkeypatch.delenv("KI_PROFILE", raising=False)


# -- precedence --

def test_flag_wins_over_binding(tmp_path):
    write_vault_marker(tmp_path, uri="v", profile="work")
    assert resolve_profile(_cfg(), "personal", start_dir=tmp_path).name == "personal"


def test_binding_used_when_no_flag(tmp_path):
    write_vault_marker(tmp_path, uri="v", profile="work")
    assert resolve_profile(_cfg(), None, start_dir=tmp_path).name == "work"


def test_binding_found_from_subdir(tmp_path):
    write_vault_marker(tmp_path, uri="v", profile="work")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert resolve_profile(_cfg(), None, start_dir=nested).name == "work"


def test_ki_profile_is_last_resort(tmp_path, monkeypatch):
    monkeypatch.setenv("KI_PROFILE", "work")
    # outside a vault, no flag → falls to $KI_PROFILE
    assert resolve_profile(_cfg(), None, start_dir=tmp_path).name == "work"


def test_binding_beats_ki_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("KI_PROFILE", "work")
    write_vault_marker(tmp_path, uri="v", profile="personal")
    assert resolve_profile(_cfg(), None, start_dir=tmp_path).name == "personal"


# -- no default, ever: unresolvable → error --

def test_no_profile_outside_vault_errors(tmp_path):
    with pytest.raises(ClickException):
        resolve_profile(_cfg(), None, start_dir=tmp_path)


def test_unbound_vault_errors(tmp_path):
    write_vault_marker(tmp_path, uri="v")  # marker with no profile bound
    with pytest.raises(ClickException):
        resolve_profile(_cfg(), None, start_dir=tmp_path)


def test_sole_profile_is_not_auto_picked(tmp_path):
    cfg = Config()
    cfg.add_profile(Profile(name="only", uri="bolt://o", user="neo4j", password="x"))
    with pytest.raises(ClickException):
        resolve_profile(cfg, None, start_dir=tmp_path)


def test_unknown_flag_errors(tmp_path):
    with pytest.raises(ClickException):
        resolve_profile(_cfg(), "nope", start_dir=tmp_path)


def test_ki_profile_unknown_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("KI_PROFILE", "ghost")
    with pytest.raises(ClickException):
        resolve_profile(_cfg(), None, start_dir=tmp_path)


def test_bound_profile_missing_from_config_raises(tmp_path):
    write_vault_marker(tmp_path, uri="v", profile="ghost")
    with pytest.raises(BoundProfileMissing):
        resolve_profile(_cfg(), None, start_dir=tmp_path)
