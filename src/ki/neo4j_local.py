"""Thin wrapper around the `neo4j-local` CLI.

We shell out — we don't reimplement lifecycle. `neo4j-local` is responsible for
download / start / stop / credentials / port allocation; we just consume its
output. See https://github.com/johnymontana/neo4j-local for the upstream.

Used by:
  - `ki configure → Local`        (one-time start, write profile)
  - integration test fixture      (ephemeral instance for tests)
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass


class Neo4jLocalNotInstalled(RuntimeError):
    """Raised when the `neo4j-local` binary isn't on PATH."""


class Neo4jLocalError(RuntimeError):
    """Raised when a `neo4j-local` subcommand fails."""


@dataclass
class LocalCredentials:
    uri: str
    user: str
    password: str


def is_installed() -> bool:
    return shutil.which("neo4j-local") is not None


def _require() -> str:
    path = shutil.which("neo4j-local")
    if not path:
        raise Neo4jLocalNotInstalled(
            "neo4j-local is not installed. Install it from "
            "https://github.com/johnymontana/neo4j-local, then re-run `ki configure`."
        )
    return path


def _run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    bin_path = _require()
    return subprocess.run(
        [bin_path, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _check_ok(proc: subprocess.CompletedProcess, what: str) -> None:
    if proc.returncode != 0:
        raise Neo4jLocalError(
            f"neo4j-local {what} failed (exit {proc.returncode}):\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )


def start(ephemeral: bool = False) -> None:
    """Start the local Neo4j instance. Idempotent if already running."""
    args = ["start"]
    if ephemeral:
        args.append("--ephemeral")
    proc = _run(args, timeout=600)
    _check_ok(proc, "start")


def stop() -> None:
    proc = _run(["stop"])
    _check_ok(proc, "stop")


def credentials() -> LocalCredentials:
    """Return the active local-Neo4j credentials.

    `neo4j-local credentials --json` is preferred. Falls back to parsing the
    plain text output if `--json` isn't supported.
    """
    proc = _run(["credentials", "--json"])
    if proc.returncode == 0 and proc.stdout.strip():
        try:
            data = json.loads(proc.stdout)
            return LocalCredentials(
                uri=data["uri"],
                user=data["user"],
                password=data["password"],
            )
        except (json.JSONDecodeError, KeyError) as exc:
            raise Neo4jLocalError(
                f"unparseable neo4j-local credentials JSON: {exc}\n{proc.stdout}"
            ) from exc
    # Plain-text fallback.
    proc = _run(["credentials"])
    _check_ok(proc, "credentials")
    uri = user = password = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.lower().startswith("uri:"):
            uri = line.split(":", 1)[1].strip()
        elif line.lower().startswith("user:"):
            user = line.split(":", 1)[1].strip()
        elif line.lower().startswith("password:"):
            password = line.split(":", 1)[1].strip()
    if not (uri and user and password):
        raise Neo4jLocalError(
            f"could not parse credentials from neo4j-local output:\n{proc.stdout}"
        )
    return LocalCredentials(uri=uri, user=user, password=password)


def status() -> str:
    proc = _run(["status"])
    return proc.stdout.strip()
