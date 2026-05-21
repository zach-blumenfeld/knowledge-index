"""Thin wrapper around `podman` for the `ki configure → Local` path.

The full runbook (commands, recovery, teardown) lives in
`references/neo4j-podman.md` — this module mirrors the same canonical values
(container name, volume, image, plugins, auth) so the doc and the code agree.

Surface kept intentionally narrow:
  - is_installed()        — is `podman` on PATH?
  - container_state()     — "running" | "stopped" | "missing"
  - ensure_running()      — idempotent: brings up Neo4j and returns creds
  - stop()                — for tests / teardown
"""

from __future__ import annotations

import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Literal

CONTAINER_NAME = "neo4j-ki"
VOLUME_NAME = "neo4j-ki-data"
IMAGE = "neo4j:latest"
BOLT_PORT = 7687
BROWSER_PORT = 7474
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "password"
# Quoted JSON list — Neo4j reads this env var literally.
PLUGINS_ENV = '["apoc","genai"]'

ContainerState = Literal["running", "stopped", "missing"]


class PodmanNotInstalled(RuntimeError):
    """Raised when the `podman` binary isn't on PATH."""


class PodmanError(RuntimeError):
    """Raised when a `podman` subcommand fails."""


class PortInUseError(RuntimeError):
    """Raised when :7687 is bound by something that isn't our container."""


@dataclass
class PodmanCredentials:
    uri: str
    user: str
    password: str


def is_installed() -> bool:
    import shutil

    return shutil.which("podman") is not None


def _require() -> None:
    if not is_installed():
        raise PodmanNotInstalled(
            "`podman` is not installed. See references/neo4j-podman.md "
            "(Preflight) for install steps — on macOS: `brew install podman` "
            "then `podman machine init && podman machine start`."
        )


def _run(args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess:
    _require()
    return subprocess.run(
        ["podman", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _check_ok(proc: subprocess.CompletedProcess, what: str) -> None:
    if proc.returncode != 0:
        raise PodmanError(
            f"podman {what} failed (exit {proc.returncode}):\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )


def container_state() -> ContainerState:
    proc = _run(
        ["inspect", CONTAINER_NAME, "--format", "{{.State.Status}}"]
    )
    if proc.returncode != 0:
        # `podman inspect` exits non-zero when the container doesn't exist.
        return "missing"
    status = proc.stdout.strip().lower()
    return "running" if status == "running" else "stopped"


def _port_bound(port: int) -> bool:
    """Return True if something is already listening on localhost:<port>."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _bolt_ready() -> bool:
    """Return True if Neo4j is actually serving Bolt queries (not just port-open).

    Neo4j opens :7687 well before it can answer queries (kernel still
    initializing, plugins still loading). The driver-level handshake fails
    if we hand it the URI too early. Use the container's own `cypher-shell`
    as a readiness probe — it succeeds only when the database is live.
    """
    proc = _run(
        [
            "exec", CONTAINER_NAME,
            "cypher-shell",
            "-u", DEFAULT_USER, "-p", DEFAULT_PASSWORD,
            "--format", "plain",
            "RETURN 1",
        ],
        timeout=10,
    )
    return proc.returncode == 0


def _wait_for_ready(timeout: int = 120) -> None:
    """Poll Neo4j until it accepts Bolt queries, or raise.

    Two-phase: first wait for :7687 to be bound (cheap), then for
    `cypher-shell RETURN 1` to succeed (the real readiness gate). Default
    timeout is 120s — first-run image pull + plugin load + kernel start
    can take well over a minute on a fresh machine.
    """
    deadline = time.monotonic() + timeout
    # Phase 1: wait for the port to open.
    while time.monotonic() < deadline:
        if _port_bound(BOLT_PORT):
            break
        time.sleep(1)
    else:
        raise PodmanError(
            f"Neo4j did not open :{BOLT_PORT} within {timeout}s. "
            "Check `podman logs neo4j-ki` and references/neo4j-podman.md."
        )
    # Phase 2: wait for actual query readiness.
    while time.monotonic() < deadline:
        if _bolt_ready():
            return
        time.sleep(2)
    raise PodmanError(
        f"Neo4j opened :{BOLT_PORT} but is not yet serving queries after "
        f"{timeout}s. Check `podman logs neo4j-ki` and "
        "references/neo4j-podman.md."
    )


def ensure_running(*, wait_seconds: int = 120) -> PodmanCredentials:
    """Bring the `neo4j-ki` container to a running state. Idempotent.

    Handles three input states:
      - running  → return creds immediately.
      - stopped  → `podman start`, wait for ready, return creds.
      - missing  → `podman run` (preflight: :7687 must be free), wait, return.

    Raises:
      PodmanNotInstalled — `podman` binary missing.
      PortInUseError     — :7687 is bound by something that isn't ours.
      PodmanError        — any other podman failure.
    """
    state = container_state()

    if state == "running":
        return _credentials()

    if state == "stopped":
        proc = _run(["start", CONTAINER_NAME])
        _check_ok(proc, "start")
        _wait_for_ready(wait_seconds)
        return _credentials()

    # state == "missing" — fresh `podman run`.
    if _port_bound(BOLT_PORT):
        raise PortInUseError(
            f"Port :{BOLT_PORT} is already in use by something other than the "
            f"`{CONTAINER_NAME}` container. Either stop that process, or use "
            "`ki configure → 3) Existing` to point at it. See "
            "references/neo4j-podman.md (Preflight)."
        )

    proc = _run(
        [
            "run",
            "-d",
            "--name", CONTAINER_NAME,
            "--restart", "unless-stopped",
            "-p", f"{BROWSER_PORT}:{BROWSER_PORT}",
            "-p", f"{BOLT_PORT}:{BOLT_PORT}",
            "-v", f"{VOLUME_NAME}:/data",
            "-e", f"NEO4J_AUTH={DEFAULT_USER}/{DEFAULT_PASSWORD}",
            "-e", f"NEO4J_PLUGINS={PLUGINS_ENV}",
            IMAGE,
        ],
        timeout=300,  # first-run image pull can be slow.
    )
    _check_ok(proc, "run")
    _wait_for_ready(wait_seconds)
    return _credentials()


def _credentials() -> PodmanCredentials:
    return PodmanCredentials(
        uri=f"bolt://localhost:{BOLT_PORT}",
        user=DEFAULT_USER,
        password=DEFAULT_PASSWORD,
    )


def stop() -> None:
    proc = _run(["stop", CONTAINER_NAME])
    _check_ok(proc, "stop")
