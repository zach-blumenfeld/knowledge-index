"""Unit tests for the neo4j-cli credential bridge (`ki.neo4j_cli`).

subprocess + which are mocked — these never touch a real neo4j-cli store.
"""

from __future__ import annotations

import os
import stat

import pytest

from ki import neo4j_cli
from ki.config import Profile


def _prof(**kw) -> Profile:
    d = dict(name="work", uri="bolt://h:7687", user="neo4j", password="s3cret")
    d.update(kw)
    return Profile(**d)


def test_is_available(monkeypatch):
    monkeypatch.setattr(neo4j_cli.shutil, "which", lambda _: "/bin/neo4j-cli")
    assert neo4j_cli.is_available() is True
    monkeypatch.setattr(neo4j_cli.shutil, "which", lambda _: None)
    assert neo4j_cli.is_available() is False


def test_register_requires_neo4j_cli(monkeypatch):
    monkeypatch.setattr(neo4j_cli.shutil, "which", lambda _: None)
    with pytest.raises(FileNotFoundError):
        neo4j_cli.register_credential(_prof())


def test_register_builds_remove_then_add_and_hides_password(monkeypatch):
    monkeypatch.setattr(neo4j_cli.shutil, "which", lambda _: "/bin/neo4j-cli")
    calls: list[list[str]] = []
    captured: dict[str, object] = {}

    def fake_run(argv, **kw):
        calls.append(argv)
        if "--env" in argv:  # the `add` call — read the temp env file it points at
            env_path = argv[argv.index("--env") + 1]
            captured["text"] = open(env_path, encoding="utf-8").read()
            captured["mode"] = stat.S_IMODE(os.stat(env_path).st_mode)

        class _R:
            returncode = 0
            stderr = ""
            stdout = ""

        return _R()

    monkeypatch.setattr(neo4j_cli.subprocess, "run", fake_run)
    neo4j_cli.register_credential(_prof(database="neo4j"))

    # Idempotent upsert: remove first, then add.
    assert calls[0][:4] == ["neo4j-cli", "credential", "dbms", "remove"]
    assert calls[1][:4] == ["neo4j-cli", "credential", "dbms", "add"]
    add = calls[1]
    assert "--name" in add and "work" in add
    assert "--env" in add and "--rw" in add
    # The password is NEVER on argv (no leak to `ps` / logs).
    assert all("s3cret" not in part for part in add)
    # It rides in a 0600 temp env file instead.
    assert "NEO4J_URI=bolt://h:7687" in captured["text"]
    assert "NEO4J_USERNAME=neo4j" in captured["text"]
    assert "NEO4J_PASSWORD=s3cret" in captured["text"]
    assert "NEO4J_DATABASE=neo4j" in captured["text"]
    assert captured["mode"] == 0o600


def test_register_omits_database_when_unset(monkeypatch):
    monkeypatch.setattr(neo4j_cli.shutil, "which", lambda _: "/bin/neo4j-cli")
    seen: dict[str, str] = {}

    def fake_run(argv, **kw):
        if "--env" in argv:
            seen["text"] = open(argv[argv.index("--env") + 1], encoding="utf-8").read()

        class _R:
            returncode = 0
            stderr = ""
            stdout = ""

        return _R()

    monkeypatch.setattr(neo4j_cli.subprocess, "run", fake_run)
    neo4j_cli.register_credential(_prof(database=None))
    assert "NEO4J_DATABASE" not in seen["text"]


def test_register_raises_on_add_failure(monkeypatch):
    monkeypatch.setattr(neo4j_cli.shutil, "which", lambda _: "/bin/neo4j-cli")

    def fake_run(argv, **kw):
        class _R:
            returncode = 0 if "remove" in argv else 1
            stderr = "boom: bad uri"
            stdout = ""

        return _R()

    monkeypatch.setattr(neo4j_cli.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError) as e:
        neo4j_cli.register_credential(_prof())
    assert "boom" in str(e.value)


def test_register_cleans_up_temp_env_file(monkeypatch, tmp_path):
    monkeypatch.setattr(neo4j_cli.shutil, "which", lambda _: "/bin/neo4j-cli")
    seen_paths: list[str] = []

    def fake_run(argv, **kw):
        if "--env" in argv:
            seen_paths.append(argv[argv.index("--env") + 1])

        class _R:
            returncode = 0
            stderr = ""
            stdout = ""

        return _R()

    monkeypatch.setattr(neo4j_cli.subprocess, "run", fake_run)
    neo4j_cli.register_credential(_prof())
    assert seen_paths and not os.path.exists(seen_paths[0])  # temp file removed
