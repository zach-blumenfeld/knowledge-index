"""CLI argument parsing smoke test for all five commands.

We use Click's CliRunner to exercise the parser only — no Neo4j contact.
"""

from click.testing import CliRunner

from ki.cli import main


def test_help_lists_all_commands():
    runner = CliRunner()
    res = runner.invoke(main, ["--help"])
    assert res.exit_code == 0
    for cmd in ("configure", "index", "search", "rm", "init"):
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
    for flag in ("--profile", "--batch-size", "--max-file-size", "--concurrency"):
        assert flag in res.output


def test_search_help_lists_type_choices():
    runner = CliRunner()
    res = runner.invoke(main, ["search", "--help"])
    assert res.exit_code == 0
    assert "section" in res.output
    assert "document" in res.output
    assert "neighbors" in res.output


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
