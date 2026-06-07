"""#54 Fix 3 — IngestServiceUnavailable + CLI render."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from ki.commands import index as index_cmd
from ki.ingest.pipeline import IngestServiceUnavailable


def test_exception_carries_run_state():
    exc = IngestServiceUnavailable(
        docs_processed=3043,
        docs_total=3269,
        profile_source="local-podman",
    )
    assert exc.docs_processed == 3043
    assert exc.docs_total == 3269
    assert exc.profile_source == "local-podman"
    assert "3043" in str(exc)
    assert "3269" in str(exc)


def _capture_render(exc: IngestServiceUnavailable, batch_size: int) -> str:
    buf = io.StringIO()
    fake = Console(file=buf, width=200, force_terminal=False, color_system=None)
    orig = index_cmd.console
    index_cmd.console = fake
    try:
        index_cmd._render_service_unavailable(exc, batch_size=batch_size)
    finally:
        index_cmd.console = orig
    return buf.getvalue()


def test_render_local_podman_points_at_canonical_container_and_runbook():
    exc = IngestServiceUnavailable(
        docs_processed=3043,
        docs_total=3269,
        profile_source="local-podman",
    )
    out = _capture_render(exc, batch_size=1000)
    # Container name + commands the SKILL.md recovery flow expects.
    assert "neo4j-ki" in out
    assert "podman start neo4j-ki" in out
    assert "podman ps -a --filter name=neo4j-ki" in out
    # Runbook pointer — same one SKILL.md sends agents to.
    assert "skills/knowledge-index/references/neo4j-podman.md" in out
    # Count carries through into the message.
    assert "3043" in out and "3269" in out
    # Generic hints should NOT show up for the Podman-specific path.
    assert "NEO4J_server_memory_heap_max__size" not in out


@pytest.mark.parametrize("source", ["aura", "existing"])
def test_render_generic_for_non_podman_sources(source: str):
    exc = IngestServiceUnavailable(
        docs_processed=500,
        docs_total=2000,
        profile_source=source,
    )
    out = _capture_render(exc, batch_size=1000)
    # Generic playbook — heap env, batch knob, split-vault advice.
    assert "NEO4J_server_memory_heap_max__size" in out
    assert "--batch-size" in out
    assert "1000" in out  # current batch size echoed
    assert "split" in out.lower()
    # No Podman-specific commands here — we don't know their container.
    assert "neo4j-ki" not in out
    assert "podman start" not in out
