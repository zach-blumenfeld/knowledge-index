"""Thin wrapper around `podman` for the `ki configure → Local` path.

The full runbook (commands, recovery, teardown) lives in
`skills/knowledge-base/references/neo4j-podman.md` — this module mirrors the same canonical values
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
# JVM heap ceiling for the canonical container. Conservatively sized for a
# personal-laptop tool — total Neo4j footprint (heap + pagecache + native
# overhead) lands around ~2 GB so `ki` is a good citizen alongside the
# user's other apps. Covers the documented v1 envelope (10k docs / 1 GB
# per vault, ingest-dominated; see docs/requirements_v01_mvp.md
# § Scalability) because the batcher's existing OOM auto-recovery
# (halve-and-retry at floor 16) absorbs the occasional fat transaction.
# Users hitting "batch size shrunk to N" warnings on huge vaults can
# override via their own `podman run -e ...` or bump --batch-size.
HEAP_MAX_SIZE = "1G"
# Page cache holds graph-store pages used during MERGE/MATCH lookups. For
# ingest workloads (write-dominated) the page cache barely matters; a
# small one keeps the total memory commit honest. Neo4j's pre-flight
# refuses to start if heap + pagecache + native > container memory, so
# this MUST be set explicitly whenever HEAP_MAX_SIZE is set.
PAGECACHE_SIZE = "512M"

ContainerState = Literal["running", "stopped", "missing"]


class PodmanNotInstalled(RuntimeError):
    """Raised when the `podman` binary isn't on PATH."""


class PodmanError(RuntimeError):
    """Raised when a `podman` subcommand fails."""


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
            "`podman` is not installed. See skills/knowledge-base/references/neo4j-podman.md "
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


def _find_free_port(start: int, *, limit: int = 200) -> int:
    """First port at or above `start` that nothing is listening on."""
    for port in range(start, start + limit):
        if not _port_bound(port):
            return port
    raise PodmanError(f"no free port found in [{start}, {start + limit}).")


def _published_bolt_port() -> int:
    """Host port mapped to the container's internal Bolt port (`7687`).

    A relocated container (created when `:7687` was busy) publishes Bolt on a
    different host port; read it back from podman rather than assuming 7687.
    """
    proc = _run(["port", CONTAINER_NAME, str(BOLT_PORT)])
    out = proc.stdout.strip()
    if proc.returncode == 0 and out:
        # e.g. "0.0.0.0:7688" (a "[::]:7688" line may follow).
        try:
            return int(out.splitlines()[0].rsplit(":", 1)[1])
        except (ValueError, IndexError):
            pass
    return BOLT_PORT


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


def _wait_for_ready(bolt_port: int, timeout: int = 90) -> None:
    """Poll Neo4j until it accepts Bolt queries, or raise.

    Two-phase: first wait for the host Bolt port to be bound (cheap), then for
    `cypher-shell RETURN 1` to succeed (the real readiness gate). Default
    timeout is 90s — kernel start + plugin load (APOC + GenAI) can take a
    while on a fresh machine. (The image pull happens earlier, inside
    `podman run`'s own timeout.)
    """
    deadline = time.monotonic() + timeout
    # Phase 1: wait for the host port to open.
    while time.monotonic() < deadline:
        if _port_bound(bolt_port):
            break
        time.sleep(1)
    else:
        raise PodmanError(
            f"Neo4j did not open :{bolt_port} within {timeout}s. "
            "Check `podman logs neo4j-ki` and skills/knowledge-base/references/neo4j-podman.md."
        )
    # Phase 2: wait for actual query readiness.
    while time.monotonic() < deadline:
        if _bolt_ready():
            return
        time.sleep(2)
    raise PodmanError(
        f"Neo4j opened :{bolt_port} but is not yet serving queries after "
        f"{timeout}s. Check `podman logs neo4j-ki` and "
        "skills/knowledge-base/references/neo4j-podman.md."
    )


def ensure_running(*, wait_seconds: int = 90) -> PodmanCredentials:
    """Bring the `neo4j-ki` container to a running state. Idempotent.

    Handles three input states:
      - running  → return creds at the container's actual published port.
      - stopped  → `podman start`, wait for ready, return creds.
      - missing  → `podman run`; if `:7687` is busy, bind the next free host
                   port instead so a stranger on 7687 never blocks bring-up.

    Raises:
      PodmanNotInstalled — `podman` binary missing.
      PodmanError        — any podman failure (incl. no free port found).
    """
    state = container_state()

    if state == "running":
        return _credentials(_published_bolt_port())

    if state == "stopped":
        proc = _run(["start", CONTAINER_NAME])
        _check_ok(proc, "start")
        bolt_port = _published_bolt_port()
        _wait_for_ready(bolt_port, wait_seconds)
        return _credentials(bolt_port)

    # state == "missing" — fresh `podman run`. Use the canonical ports when
    # free; otherwise relocate to the next free host port(s) so an unrelated
    # service on 7687 doesn't block us.
    bolt_port = BOLT_PORT if not _port_bound(BOLT_PORT) else _find_free_port(BOLT_PORT + 1)
    browser_port = (
        BROWSER_PORT if not _port_bound(BROWSER_PORT) else _find_free_port(BROWSER_PORT + 1)
    )

    proc = _run(
        [
            "run",
            "-d",
            "--name", CONTAINER_NAME,
            "--restart", "unless-stopped",
            "-p", f"{browser_port}:{BROWSER_PORT}",
            "-p", f"{bolt_port}:{BOLT_PORT}",
            "-v", f"{VOLUME_NAME}:/data",
            "-e", f"NEO4J_AUTH={DEFAULT_USER}/{DEFAULT_PASSWORD}",
            "-e", f"NEO4J_PLUGINS={PLUGINS_ENV}",
            "-e", f"NEO4J_server_memory_heap_max__size={HEAP_MAX_SIZE}",
            "-e", f"NEO4J_server_memory_pagecache_size={PAGECACHE_SIZE}",
            IMAGE,
        ],
        timeout=300,  # first-run image pull can be slow.
    )
    _check_ok(proc, "run")
    _wait_for_ready(bolt_port, wait_seconds)
    return _credentials(bolt_port)


def _credentials(bolt_port: int = BOLT_PORT) -> PodmanCredentials:
    return PodmanCredentials(
        uri=f"bolt://localhost:{bolt_port}",
        user=DEFAULT_USER,
        password=DEFAULT_PASSWORD,
    )


def stop() -> None:
    proc = _run(["stop", CONTAINER_NAME])
    _check_ok(proc, "stop")
