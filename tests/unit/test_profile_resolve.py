"""Profile resolution precedence: flag > vault binding > config default."""

import pytest

from ki.config import Config, Profile
from ki.profile_resolve import BoundProfileMissing, resolve_profile
from ki.vault import write_vault_marker


def _cfg():
    cfg = Config()
    cfg.add_profile(Profile(name="personal", uri="bolt://p", user="neo4j", password="x"))
    cfg.add_profile(Profile(name="work", uri="bolt://w", user="neo4j", password="x"))
    cfg.default_profile = "personal"
    return cfg


def test_flag_wins_over_binding(tmp_path):
    write_vault_marker(tmp_path, uri="v", profile="work")
    prof = resolve_profile(_cfg(), "personal", start_dir=tmp_path)
    assert prof.name == "personal"


def test_binding_used_when_no_flag(tmp_path):
    write_vault_marker(tmp_path, uri="v", profile="work")
    prof = resolve_profile(_cfg(), None, start_dir=tmp_path)
    assert prof.name == "work"


def test_binding_found_from_subdir(tmp_path):
    write_vault_marker(tmp_path, uri="v", profile="work")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    prof = resolve_profile(_cfg(), None, start_dir=nested)
    assert prof.name == "work"


def test_falls_back_to_config_default_outside_vault(tmp_path):
    prof = resolve_profile(_cfg(), None, start_dir=tmp_path)
    assert prof.name == "personal"


def test_unbound_vault_falls_back_to_default(tmp_path):
    write_vault_marker(tmp_path, uri="v")  # no profile bound
    prof = resolve_profile(_cfg(), None, start_dir=tmp_path)
    assert prof.name == "personal"


def test_bound_profile_missing_from_config_raises(tmp_path):
    write_vault_marker(tmp_path, uri="v", profile="ghost")
    with pytest.raises(BoundProfileMissing):
        resolve_profile(_cfg(), None, start_dir=tmp_path)
