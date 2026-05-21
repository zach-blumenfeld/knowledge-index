"""Progress reporter protocol for `ki index` (#53).

`ingest_vault` calls these hooks at phase boundaries; the default
`NullProgressReporter` is a no-op (used by tests and non-TTY runs). The CLI
installs a `rich`-backed implementation that drives a live progress bar.
"""

from __future__ import annotations

from typing import Protocol


class ProgressReporter(Protocol):
    """Phase-boundary hooks consumed by `ingest_vault`.

    Three phases match the actual ingest shape:
      1. Reading files (concurrent read).
      2. Processing docs (parse + per-doc Neo4j write; the dominant phase).
      3. Finalizing (LINKS_TO + stub/external doc materialization + aliases).
    """

    def reading_start(self, total: int) -> None: ...
    def reading_done(self) -> None: ...
    def docs_start(self, total: int) -> None: ...
    def doc_processed(self, kind: str) -> None: ...  # "added" | "updated" | "skipped"
    def docs_done(self) -> None: ...
    def finalize_start(self) -> None: ...
    def finalize_done(self) -> None: ...


class NullProgressReporter:
    """No-op reporter; used when no UI is attached (tests, non-TTY)."""

    def reading_start(self, total: int) -> None:
        pass

    def reading_done(self) -> None:
        pass

    def docs_start(self, total: int) -> None:
        pass

    def doc_processed(self, kind: str) -> None:
        pass

    def docs_done(self) -> None:
        pass

    def finalize_start(self) -> None:
        pass

    def finalize_done(self) -> None:
        pass
