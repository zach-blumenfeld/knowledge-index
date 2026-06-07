"""CLI argument parsing smoke test for all five commands.

We use Click's CliRunner to exercise the parser only — no Neo4j contact.
"""

from click.testing import CliRunner

from ki.cli import main


def test_help_lists_all_commands():
    runner = CliRunner()
    res = runner.invoke(main, ["--help"])
    assert res.exit_code == 0
    for cmd in ("configure", "index", "search", "drop", "init", "vault"):
        assert cmd in res.output


def test_version_flag():
    runner = CliRunner()
    res = runner.invoke(main, ["--version"])
    assert res.exit_code == 0


def test_index_requires_path():
    runner = CliRunner()
    res = runner.invoke(main, ["index"])
    assert res.exit_code != 0


def test_index_help_lists_flags():
    runner = CliRunner()
    res = runner.invoke(main, ["index", "--help"])
    assert res.exit_code == 0
    for flag in (
        "--profile",
        "--batch-size",
        "--max-file-size",
        "--concurrency",
        "--description",
        "--force-description",
    ):
        assert flag in res.output


def test_index_force_description_requires_description(tmp_path):
    """`--force-description` without `--description` is nonsensical and must error."""
    runner = CliRunner()
    res = runner.invoke(
        main, ["index", "--force-description", str(tmp_path)]
    )
    assert res.exit_code != 0
    assert "--force-description requires --description" in res.output


def test_search_help_lists_types_flag_and_valid_values():
    runner = CliRunner()
    res = runner.invoke(main, ["search", "--help"])
    assert res.exit_code == 0
    # New 0.4.0 surface: --types (plural) replaces --type. Default = all three.
    assert "--types" in res.output
    for choice in ("section", "document", "vault"):
        assert choice in res.output
    # `neighbors` was dropped in 0.4.0 — see #33 / #35.
    assert "neighbors" not in res.output


def test_vault_group_help_lists_subcommands():
    runner = CliRunner()
    res = runner.invoke(main, ["vault", "--help"])
    assert res.exit_code == 0
    assert "list" in res.output


def test_vault_list_help_works():
    runner = CliRunner()
    res = runner.invoke(main, ["vault", "list", "--help"])
    assert res.exit_code == 0
    for flag in ("--profile", "--json"):
        assert flag in res.output


def test_drop_help_lists_safety_flags():
    """`ki drop` is vault-only in 0.4.0 — see docs/index_rm_behavior.md.

    Removed flag: `--vault` (the command is vault-only now; flag is redundant).
    """
    runner = CliRunner()
    res = runner.invoke(main, ["drop", "--help"])
    assert res.exit_code == 0
    for flag in ("--dry-run", "--yes", "--keep-marker", "--chunk-size"):
        assert flag in res.output
    # NEVER expose --purge per the requirements.
    assert "--purge" not in res.output
    # `--vault` no longer exists — vault is the only mode.
    assert "--vault" not in res.output


def test_nuke_help_lists_safety_flags():
    runner = CliRunner()
    res = runner.invoke(main, ["nuke", "--help"])
    assert res.exit_code == 0
    for flag in ("--dry-run", "--yes", "--keep-marker", "--chunk-size"):
        assert flag in res.output


def test_init_help_works():
    runner = CliRunner()
    res = runner.invoke(main, ["init", "--help"])
    assert res.exit_code == 0


# ---- ki outline / ki tree (alias + positional URI) -----------------------


def test_outline_appears_in_top_level_help():
    """`ki outline` is the canonical command; `ki tree` (the v0.4.x name) is
    a hidden alias that should NOT show up in the top-level help listing."""
    runner = CliRunner()
    res = runner.invoke(main, ["--help"])
    assert res.exit_code == 0
    assert "outline" in res.output
    # `tree` is hidden so it doesn't show up as a separate command in the
    # listing — keeps the surface uncluttered while still working when typed.
    # Allow it to appear elsewhere in the help (e.g. inside `outline`'s help
    # blurb), but not as its own bullet.
    listed = [line for line in res.output.splitlines() if line.startswith("  tree")]
    assert listed == []


def test_outline_help_lists_positional_uri_and_flags():
    runner = CliRunner()
    res = runner.invoke(main, ["outline", "--help"])
    assert res.exit_code == 0
    # Positional URI argument (the new canonical shape).
    assert "URI" in res.output or "[URI]" in res.output or "uri" in res.output.lower()
    # Back-compat flag still present.
    assert "--at" in res.output
    for flag in ("--profile", "--depth", "--full"):
        assert flag in res.output


def test_tree_alias_still_works():
    """`ki tree` is kept as a permanent alias for `ki outline`."""
    runner = CliRunner()
    res = runner.invoke(main, ["tree", "--help"])
    assert res.exit_code == 0
    # The alias accepts the same flags.
    for flag in ("--at", "--depth", "--full"):
        assert flag in res.output


def test_outline_accepts_positional_uri_without_at_flag(monkeypatch):
    """`ki outline <uri>` binds the positional argument and flows it
    through to cmd_outline's `at=` parameter. We stub cmd_outline so the test
    is a pure parser check — without the stub, Click's CliRunner runs the
    full callback, which connects to the user's real Neo4j (or hangs
    waiting on it)."""
    captured: dict = {}

    def fake_cmd_outline(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("ki.cli.cmd_outline", fake_cmd_outline)
    runner = CliRunner()
    res = runner.invoke(main, ["outline", "vault://test-uri"])
    assert res.exit_code == 0, res.output
    # Positional URI binds to `at` after the (uri or at_flag) fallback.
    assert captured.get("at") == "vault://test-uri"


def test_outline_accepts_at_flag_for_backcompat(monkeypatch):
    captured: dict = {}

    def fake_cmd_outline(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("ki.cli.cmd_outline", fake_cmd_outline)
    runner = CliRunner()
    res = runner.invoke(main, ["outline", "--at", "vault://test-uri"])
    assert res.exit_code == 0, res.output
    assert captured.get("at") == "vault://test-uri"


def test_outline_positional_uri_wins_over_at_flag(monkeypatch):
    """When both forms are passed, the positional URI takes precedence —
    the `--at` flag is the fallback, not an override."""
    captured: dict = {}

    def fake_cmd_outline(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("ki.cli.cmd_outline", fake_cmd_outline)
    runner = CliRunner()
    res = runner.invoke(
        main,
        ["outline", "vault://positional", "--at", "vault://flag"],
    )
    assert res.exit_code == 0, res.output
    assert captured.get("at") == "vault://positional"


def test_tree_alias_accepts_positional_uri(monkeypatch):
    """Positional URI works under the `ki tree` alias too."""
    captured: dict = {}

    def fake_cmd_outline(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("ki.cli.cmd_outline", fake_cmd_outline)
    runner = CliRunner()
    res = runner.invoke(main, ["tree", "vault://test-uri"])
    assert res.exit_code == 0, res.output
    assert captured.get("at") == "vault://test-uri"
