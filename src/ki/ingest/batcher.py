"""Batched UNWIND writes with Neo4j-OOM auto-recovery.

The pipeline ships rows to Cypher queries via `UNWIND $rows AS row`. This
module slices a long list into batches of a configured size and runs each
batch as a separate transaction.

If Neo4j raises `TransientError: ... Out of memory`, we halve the batch
size, retry the failed slice, and continue with the smaller size for
subsequent batches (with one user-visible warning). Per docs/requirements_v01_mvp.md
*Two kinds of OOM* — this is the recoverable case.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from neo4j.exceptions import TransientError

log = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 1000
MIN_BATCH_SIZE = 16
_OOM_MARKER = "out of memory"


def _is_oom(exc: BaseException) -> bool:
    return isinstance(exc, TransientError) and _OOM_MARKER in str(exc).lower()


def chunks(rows: list[Any], size: int) -> Iterable[list[Any]]:
    if size <= 0:
        raise ValueError("batch size must be positive")
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def run_batched(
    session: Any,
    query: str,
    rows_param_name: str,
    rows: list[dict],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    extra_params: dict | None = None,
    on_shrink: Any = None,  # callable(new_size: int) -> None — emits the warning once
) -> int:
    """Run a parameterized UNWIND query in chunks.

    Returns the number of rows written.

    On `TransientError(Out of memory)`:
        1. Halve the batch size (floor of MIN_BATCH_SIZE).
        2. Retry the failing chunk with the smaller size.
        3. Continue subsequent batches with the smaller size.
        4. Emit one warning via `on_shrink` (callable) on the first shrink.
    """
    if not rows:
        return 0

    extra_params = extra_params or {}
    current_size = batch_size
    shrunk_once = False
    written = 0
    i = 0
    n = len(rows)
    while i < n:
        end = min(i + current_size, n)
        slice_rows = rows[i:end]
        params = {rows_param_name: slice_rows, **extra_params}
        try:
            session.run(query, **params).consume()
            i = end
            written += len(slice_rows)
        except TransientError as exc:
            if not _is_oom(exc):
                raise
            new_size = max(MIN_BATCH_SIZE, current_size // 2)
            if new_size == current_size:
                # We've already shrunk to MIN_BATCH_SIZE and still OOM —
                # nothing more to try.
                raise
            current_size = new_size
            if not shrunk_once and on_shrink is not None:
                try:
                    on_shrink(current_size)
                except Exception:  # noqa: BLE001
                    log.exception("on_shrink callback raised; continuing")
            else:
                log.warning("Neo4j OOM — shrinking batch to %d", current_size)
            shrunk_once = True
            # Loop continues with smaller current_size; same `i` so the failed
            # slice is re-attempted.
    return written
