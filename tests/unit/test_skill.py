"""`ki skill <subcommand>` unit tests.

Covers: bundled-skill discovery (dev fallback fires when the wheel-only
`_resources/SKILL.md` isn't present); catalog completeness; install / remove
idempotency against an isolated HOME for multiple agents; the `--path`
escape hatch; CLI argument parsing; unknown-agent error path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from ki.cli import main
from ki.commands import skill as skill_mod

# --- bundled-skill discovery ------------------------------------------------


def test_read_bundled_skill_returns_canonical_text():
    body = skill_mod.read_bundled_skill()
    assert "knowledge graph index" in body
    assert "Trigger When" in body  # routing-rule heading from skills/knowledge-index/SKILL.md


def test_read_bundled_skill_dev_fallback_resolves_to_repo_path():
    """In an editable / dev checkout there's no `_resources/SKILL.md`, so the
    fallback should walk up to `<repo>/skills/knowledge-index/SKILL.md` and read it."""
    repo_root = Path(skill_mod.__file__).resolve().parents[3]
    canonical = repo_root / "skills" / "knowledge-index" / "SKILL.md"
    assert canonical.is_file()
    assert skill_mod.read_bundled_skill() == canonical.read_text(encoding="utf-8")


# --- catalog ----------------------------------------------------------------


def test_catalog_includes_expected_agents():
    """We mirror neo4j-cli's 10-agent catalog so users have one mental model."""
    expected = {
        "claude-code", "cursor", "windsurf", "copilot",
        "gemini-cli", "cline", "codex", "pi", "opencode", "junie",
    }
    assert expected.issubset(set(skill_mod.AGENTS))


def test_expand_path_handles_tilde_xdg_and_literals(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert skill_mod._expand("~") == tmp_path
    assert skill_mod._expand("~/.claude") == tmp_path / ".claude"
    # XDG fallback resolves to $HOME/.config
    assert skill_mod._expand("$XDG_CONFIG_HOME/opencode") == tmp_path / ".config" / "opencode"


def test_expand_path_xdg_env_wins_over_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert skill_mod._expand("$XDG_CONFIG_HOME/opencode") == tmp_path / "xdg" / "opencode"


# --- install / remove against an isolated HOME ------------------------------


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return tmp_path


def _expected_skill_path(home: Path, agent: str) -> Path:
    """Manual paths from the AGENTS catalog. Catches drift if the table changes."""
    n = skill_mod.TOOL_NAME
    paths = {
        "claude-code": home / ".claude" / "skills" / n / "SKILL.md",
        "cursor": home / ".cursor" / "skills" / n / "SKILL.md",
        "windsurf": home / ".codeium" / "windsurf" / "skills" / n / "SKILL.md",
        "copilot": home / ".copilot" / "skills" / n / "SKILL.md",
        "gemini-cli": home / ".gemini" / "skills" / n / "SKILL.md",
        "cline": home / ".agents" / "skills" / n / "SKILL.md",
        "codex": home / ".codex" / "skills" / n / "SKILL.md",
        "pi": home / ".pi" / "agent" / "skills" / n / "SKILL.md",
        "opencode": home / "xdg" / "opencode" / "skills" / n / "SKILL.md",
        "junie": home / ".junie" / "skills" / n / "SKILL.md",
    }
    return paths[agent]


@pytest.mark.parametrize(
    "agent",
    ["claude-code", "cursor", "windsurf", "copilot", "gemini-cli",
     "cline", "codex", "pi", "opencode", "junie"],
)
def test_install_writes_skill_for_every_agent(agent, fake_home):
    """Every catalog entry must install to a deterministic location and write
    the bundled SKILL.md byte-for-byte."""
    rc = skill_mod.cmd_install(agent)
    assert rc == 0
    target = _expected_skill_path(fake_home, agent)
    assert target.is_file(), f"{agent}: expected {target}"
    assert target.read_text(encoding="utf-8") == skill_mod.read_bundled_skill()


def test_install_case_insensitive_agent_name(fake_home):
    rc = skill_mod.cmd_install("Claude-Code")  # case-insensitive lookup
    assert rc == 0
    assert _expected_skill_path(fake_home, "claude-code").is_file()


def test_install_is_idempotent(fake_home):
    skill_mod.cmd_install("claude-code")
    skill_mod.cmd_install("claude-code")  # re-running must not raise
    assert _expected_skill_path(fake_home, "claude-code").is_file()


def test_install_creates_missing_parent_dirs(fake_home):
    skill_mod.cmd_install("claude-code")
    assert _expected_skill_path(fake_home, "claude-code").parent.is_dir()


def test_remove_deletes_file_and_empty_per_tool_dir(fake_home):
    skill_mod.cmd_install("claude-code")
    rc = skill_mod.cmd_remove("claude-code")
    assert rc == 0
    target = _expected_skill_path(fake_home, "claude-code")
    assert not target.exists()
    # Per-tool dir (`~/.claude/skills/knowledge-index/`) is cleaned up; the per-agent
    # `skills/` dir is left alone (other tools may live in it).
    assert not target.parent.exists()


def test_remove_is_idempotent_when_nothing_installed(fake_home):
    rc = skill_mod.cmd_remove("claude-code")
    assert rc == 0


def test_remove_without_args_when_nothing_installed(fake_home):
    rc = skill_mod.cmd_remove(None)
    assert rc == 0


def test_install_without_args_uses_detected_agents(fake_home):
    """Pre-create the marker dirs for two agents; install with no arg should
    write into both and skip the rest."""
    (fake_home / ".claude").mkdir()
    (fake_home / ".cursor").mkdir()
    rc = skill_mod.cmd_install(None)
    assert rc == 0
    assert _expected_skill_path(fake_home, "claude-code").is_file()
    assert _expected_skill_path(fake_home, "cursor").is_file()
    # An undetected agent should not have been touched.
    assert not _expected_skill_path(fake_home, "codex").is_file()


def test_install_without_args_requires_a_detected_agent(fake_home):
    """No detected agents (fresh empty HOME) → install with no arg must error."""
    with pytest.raises(SystemExit) as exc_info:
        skill_mod.cmd_install(None)
    msg = str(exc_info.value.code).lower()
    assert "supported agents" in msg or "--path" in msg


def test_install_unknown_agent_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        skill_mod.cmd_install("bogus-agent")
    assert "bogus-agent" in str(exc_info.value.code)


# --- --path escape hatch ----------------------------------------------------


def test_install_path_overrides_catalog(fake_home, tmp_path):
    target = tmp_path / "out" / "MY_SKILL.md"
    rc = skill_mod.cmd_install(None, path=target)
    assert rc == 0
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == skill_mod.read_bundled_skill()


def test_install_path_with_agent_name_labels_output(fake_home, tmp_path):
    target = tmp_path / "out" / "custom.md"
    rc = skill_mod.cmd_install("my-fork-of-claude", path=target)
    # No catalog lookup, so an unknown agent name is fine when --path is set.
    assert rc == 0
    assert target.is_file()


# --- CLI integration --------------------------------------------------------


def test_cli_skill_group_lists_subcommands():
    res = CliRunner().invoke(main, ["skill", "--help"])
    assert res.exit_code == 0
    for sub in ("install", "remove", "list", "print"):
        assert sub in res.output


def test_cli_skill_install_help_shows_path_flag():
    res = CliRunner().invoke(main, ["skill", "install", "--help"])
    assert res.exit_code == 0
    assert "--path" in res.output


def test_cli_skill_print_emits_to_stdout():
    res = CliRunner().invoke(main, ["skill", "print"])
    assert res.exit_code == 0
    assert "knowledge graph index" in res.output


def test_cli_skill_list_runs_and_shows_all_agents():
    res = CliRunner().invoke(main, ["skill", "list"])
    assert res.exit_code == 0
    for name in ("claude-code", "cursor", "windsurf", "codex", "opencode"):
        assert name in res.output
