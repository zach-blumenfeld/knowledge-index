"""CLI argument parsing smoke test for all five commands.

We use Click's CliRunner to exercise the parser only — no Neo4j contact.
"""

from click.testing import CliRunner

from ki.cli import main


def test_help_lists_all_commands():
    runner = CliRunner()
    res = runner.invoke(main, ["--help"])
    assert res.exit_code == 0
    for cmd in ("configure", "index", "search", "rm", "init", "vault"):
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


def test_search_help_lists_type_choices():
    runner = CliRunner()
    res = runner.invoke(main, ["search", "--help"])
    assert res.exit_code == 0
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


def test_rm_help_lists_safety_flags():
    runner = CliRunner()
    res = runner.invoke(main, ["rm", "--help"])
    assert res.exit_code == 0
    for flag in ("--vault", "--dry-run", "--yes", "--keep-marker"):
        assert flag in res.output
    # NEVER expose --purge per the requirements.
    assert "--purge" not in res.output


def test_init_help_works():
    runner = CliRunner()
    res = runner.invoke(main, ["init", "--help"])
    assert res.exit_code == 0
